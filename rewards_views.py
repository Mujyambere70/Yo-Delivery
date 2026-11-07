"""
rewards_views.py - Yo Rewards pages: points, the daily quiz, rewards and personal coupons.
"""

from datetime import date

from flask import flash, redirect, render_template, request, url_for

import rewards as R
import world
from core import ShopError


def register(app, store, h):
    login_required, user_id, act = h.login_required, h.user_id, h.act

    @app.route("/rewards")
    def rewards_home():
        uid = user_id()
        today = store.quiz_today(uid, None) if uid else None
        return render_template("rewards/home.html", points=store.points(uid) if uid else 0, today=today,
                               coupons=store.my_coupons(uid) if uid else [], history=store.points_history(uid, 20) if uid else [],
                               leaders=store.leaderboard(), REWARDS=R.REWARDS, R=R)

    @app.route("/rewards/quiz", methods=["GET", "POST"])
    @login_required
    def rewards_quiz():
        uid = user_id()
        if request.method == "POST":
            answers = {k[1:]: v for k, v in request.form.items() if k.startswith("q")}
            try:
                result = store.finish_quiz(uid, answers)
                act("Rewards", f"Daily quiz: {result['correct']} right, {result['points']} points")
                if result["late"]:
                    flash(f"Time is over ({R.QUIZ_MINUTES} minutes) - the answers were not counted.", "error")
                elif result["points"]:
                    flash(f"You earned {result['points']} points!", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("rewards_quiz"))
        result = store.quiz_result(uid)
        if result:
            return render_template("rewards/quiz.html", result=result, questions=None, attempt=None, R=R)
        try:
            attempt, questions = store.start_quiz(uid, world.current_lang())
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("rewards_home"))
        from datetime import datetime
        used = (datetime.now() - datetime.strptime(attempt["started_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        return render_template("rewards/quiz.html", result=None, questions=questions, attempt=attempt, R=R,
                               english=attempt["lang"] != world.current_lang(),
                               seconds_left=max(0, int(R.QUIZ_MINUTES * 60 - used)))

    @app.route("/rewards/redeem", methods=["POST"])
    @login_required
    def rewards_redeem():
        try:
            got = store.redeem(user_id(), request.form.get("reward", ""))
            if got["kind"] == "wallet":
                flash(f"{world.price(got['amount'])} added to your Yo Pay wallet.", "success")
            else:
                flash(f"Your coupon {got['code']} is ready. Use it at checkout.", "success")
            act("Rewards", f"Redeemed {request.form.get('reward')}")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("rewards_home") + "#coupons")
