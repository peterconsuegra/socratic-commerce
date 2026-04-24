# app/routes/rankings.py
import logging
import os

from flask import jsonify, render_template
from flask_login import login_required

from app.models import Option
from app.services.performance import get_weekly_order_stats
from app.services.rankings import (
    get_order_percentage_by_city,
    get_top_10_months_by_sales,
    get_top_days_of_month_by_gender,
    get_top_days_of_the_week,
    get_top_hours_by_gender,
    get_top_ten_fridays_by_sales,
    get_top_ten_mondays_by_sales,
    get_top_ten_saturdays_by_sales,
    get_top_ten_sundays_by_sales,
    get_top_ten_thursdays_by_sales,
    get_top_ten_tuesdays_by_sales,
    get_top_ten_utm_campaigns,
    get_top_ten_utm_content_by_sales,
    get_top_ten_utm_medium_by_sales,
    get_top_ten_utm_source_by_sales,
    get_top_ten_utm_term_by_sales,
    get_top_ten_wednesdays_by_sales,
    get_top_twenty_days_by_sales,
    get_top_twenty_days_by_undefined_campaign,
    get_top_cities_by_gender,
    get_utm_answer_ranking,
    get_utm_content_ranking_by_gender,
)
from app.services.repurchases import print_customers_with_multiple_purchases

from . import main

logger = logging.getLogger(__name__)


@main.route("/performance")
@login_required
def performance():
    date_range_opt = Option.query.filter_by(meta_key="date_range_performance_orders.csv").first()
    date_range = date_range_opt.meta_value if date_range_opt else None

    start_date_opt = Option.query.filter_by(meta_key="start_date_performance_orders.csv").first()
    start_date = start_date_opt.meta_value if start_date_opt else None

    end_date_opt = Option.query.filter_by(meta_key="end_date_performance_orders.csv").first()
    end_date = end_date_opt.meta_value if end_date_opt else None

    input_file = "data/performance_orders.csv"
    weekly_stats = get_weekly_order_stats(input_file)

    return render_template(
        "performance.html",
        weekly_stats=weekly_stats,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
    )


@main.route("/ads_rankings")
@login_required
def ads_rankings():
    input_file = "data/ads_rankings.csv"

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_ads_rankings.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_ads_rankings.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_ads_rankings.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None

        top_ten_utm_campaigns = get_top_ten_utm_campaigns(input_file)
        top_utm_answers = get_utm_answer_ranking(input_file)
        top_twenty_days_by_undefined_campaign = get_top_twenty_days_by_undefined_campaign(input_file)
        top_utm_content = get_top_ten_utm_content_by_sales(input_file)
        top_utm_source = get_top_ten_utm_source_by_sales(input_file)
        top_utm_medium = get_top_ten_utm_medium_by_sales(input_file)
        top_utm_term = get_top_ten_utm_term_by_sales(input_file)

        utm_content_ranking_by_gender_male = get_utm_content_ranking_by_gender(
            input_file,
            gender="male",
        )
        utm_content_ranking_by_gender_female = get_utm_content_ranking_by_gender(
            input_file,
            gender="female",
        )

    except Exception as e:
        logger.exception("ads_rankings view failed")

        return render_template(
            "ads_rankings.html",
            error=str(e),
            top_utm_answers=None,
            top_ten_utm_campaigns=None,
            top_twenty_days_by_undefined_campaign=None,
            top_utm_content=None,
            top_utm_source=None,
            top_utm_medium=None,
            top_utm_term=None,
            utm_content_ranking_by_gender_male=None,
            utm_content_ranking_by_gender_female=None,
        )

    return render_template(
        "ads_rankings.html",
        top_ten_utm_campaigns=top_ten_utm_campaigns,
        top_utm_answers=top_utm_answers,
        top_twenty_days_by_undefined_campaign=top_twenty_days_by_undefined_campaign,
        top_utm_content=top_utm_content,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        top_utm_source=top_utm_source,
        top_utm_medium=top_utm_medium,
        top_utm_term=top_utm_term,
        utm_content_ranking_by_gender_male=utm_content_ranking_by_gender_male,
        utm_content_ranking_by_gender_female=utm_content_ranking_by_gender_female,
    )


@main.route("/rankings")
@login_required
def rankings():
    input_file = "data/rankings_orders.csv"
    repeated_customers_file = "data/repeated_customers.csv"

    try:
        date_range = Option.query.filter_by(meta_key="date_range_repurchases_orders.csv").first()

        if date_range:
            date_range = date_range.meta_value

        start_date = Option.query.filter_by(meta_key="start_date_repurchases_orders.csv").first()

        if start_date:
            start_date = start_date.meta_value

        end_date = Option.query.filter_by(meta_key="end_date_repurchases_orders.csv").first()

        if end_date:
            end_date = end_date.meta_value

        top_cities = get_top_cities_by_gender(input_file)
        top_hours = get_top_hours_by_gender(input_file)
        top_days_of_the_week = get_top_days_of_the_week(input_file)
        top_days_of_the_month = get_top_days_of_month_by_gender(input_file)
        top_months = get_top_10_months_by_sales(repeated_customers_file, input_file)
        top_twenty_days = get_top_twenty_days_by_sales(input_file)
        top_ten_mondays = get_top_ten_mondays_by_sales(input_file)
        top_ten_tuesdays = get_top_ten_tuesdays_by_sales(input_file)
        top_ten_wednesdays = get_top_ten_wednesdays_by_sales(input_file)
        top_ten_thursdays = get_top_ten_thursdays_by_sales(input_file)
        top_ten_fridays = get_top_ten_fridays_by_sales(input_file)
        top_ten_saturdays = get_top_ten_saturdays_by_sales(input_file)
        top_ten_sundays = get_top_ten_sundays_by_sales(input_file)
        orders_percentage = get_order_percentage_by_city(input_file)

    except Exception as e:
        logger.exception("rankings view failed")

        return render_template(
            "rankings.html",
            error=str(e),
            top_cities=None,
            top_hours=None,
            top_days_of_the_week=None,
            top_days_of_the_month=None,
            top_months=None,
            top_twenty_days=None,
            top_ten_mondays=None,
            top_ten_tuesdays=None,
            top_ten_wednesdays=None,
            top_ten_thursdays=None,
            top_ten_fridays=None,
            top_ten_saturdays=None,
            top_ten_sundays=None,
            top_ten_utm_campaigns=None,
            top_utm_answers=None,
            top_twenty_days_by_undefined_campaign=None,
            orders_percentage=None,
        )

    return render_template(
        "rankings.html",
        top_cities=top_cities,
        top_hours=top_hours,
        top_days_of_the_week=top_days_of_the_week,
        top_days_of_the_month=top_days_of_the_month,
        top_months=top_months,
        top_twenty_days=top_twenty_days,
        top_ten_mondays=top_ten_mondays,
        top_ten_tuesdays=top_ten_tuesdays,
        top_ten_wednesdays=top_ten_wednesdays,
        top_ten_thursdays=top_ten_thursdays,
        top_ten_fridays=top_ten_fridays,
        top_ten_saturdays=top_ten_saturdays,
        top_ten_sundays=top_ten_sundays,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        orders_percentage=orders_percentage,
    )


@main.route("/generate_repeated_customers", methods=["POST"])
@login_required
def generate_repeated_customers():
    input_file = "data/all_orders.csv"
    repeated_customers_file = "data/repeated_customers.csv"

    try:
        print_customers_with_multiple_purchases(input_file, repeated_customers_file)

    except Exception:
        logger.exception("Failed generating repeated customers file")

        return jsonify({
            "status": "success",
            "message": "error",
        }), 200

    return jsonify({
        "status": "success",
        "message": f"{repeated_customers_file} created",
    }), 200