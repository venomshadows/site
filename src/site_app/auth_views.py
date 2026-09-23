"""Маршруты двухэтапного входа."""
from flask import Blueprint, redirect, render_template, request, session, url_for
from site_app import auth

auth_bp = Blueprint("auth", __name__)

_RATE_LIMITED = "Слишком много попыток, попробуй позже"
_BAD_CREDENTIALS = "Неверный логин или пароль"
_BAD_SECOND_PASSWORD = "Неверный пароль"

@auth_bp.get("/login")
@auth.no_store
def login():
    if session.get("stage") == auth.STAGE_FULL:
        return redirect(url_for("pages.index"))
    return render_template("login.html", error=None)

@auth_bp.post("/login")
@auth.no_store
def login_submit():
    if auth.is_rate_limited():
        return render_template("login.html", error=_RATE_LIMITED), 429

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if auth.check_first_factor(username, password):
        # Счётчик попыток здесь НЕ сбрасываем — он общий на оба шага и
        # обнуляется только после полного входа (см. login2_submit).
        # Иначе знающий первый пароль перебирал бы второй бесконечно:
        # четыре неверные попытки, потом успешный первый шаг — и лимит
        # снова чист.
        session.clear()
        session["stage"] = auth.STAGE_FIRST
        return redirect(url_for("auth.login2"))

    auth.record_failed_attempt()
    return render_template("login.html", error=_BAD_CREDENTIALS), 401

@auth_bp.get("/login2")
@auth.no_store
def login2():
    stage = session.get("stage")
    if stage == auth.STAGE_FULL:
        return redirect(url_for("pages.index"))
    if stage != auth.STAGE_FIRST:
        return redirect(url_for("auth.login"))
    return render_template("login2.html", error=None)

@auth_bp.post("/login2")
@auth.no_store
def login2_submit():
    if session.get("stage") not in (auth.STAGE_FIRST, auth.STAGE_FULL):
        return redirect(url_for("auth.login"))

    if auth.is_rate_limited():
        return render_template("login2.html", error=_RATE_LIMITED), 429

    password = request.form.get("password", "")
    if auth.check_second_factor(password):
        auth.clear_attempts()
        session["stage"] = auth.STAGE_FULL
        session.permanent = True
        return redirect(url_for("pages.index"))

    auth.record_failed_attempt()
    return render_template("login2.html", error=_BAD_SECOND_PASSWORD), 401

@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

