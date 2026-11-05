"""Moya Pribyl: same-origin form and durable PostgreSQL storage."""
import csv
import hmac
import io
import os
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory
import psycopg

ROOT = Path(__file__).resolve().parent
CONSENT_VERSION = "2026-11-05-v1"
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 8192


def enabled():
    return os.environ.get("LEADS_ENABLED") == "true"


def connection():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def store_lead(data):
    # The transaction commits before success is returned to the browser.
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS moya_pribyl")
            cur.execute("""CREATE TABLE IF NOT EXISTS moya_pribyl.leads (
                id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                name TEXT NOT NULL, contact TEXT NOT NULL, source TEXT NOT NULL,
                consent_version TEXT NOT NULL, consent_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            cur.execute("DELETE FROM moya_pribyl.leads WHERE created_at < now() - interval '90 days'")
            cur.execute("""INSERT INTO moya_pribyl.leads
                (id,name,contact,source,consent_version) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING""",
                (data["id"],data["name"],data["contact"],data["source"],CONSENT_VERSION))


@app.after_request
def headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith(("/api/", "/admin/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/privacy.html")
@app.get("/consent.html")
def document():
    return send_from_directory(ROOT, request.path.lstrip("/"))


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/api/status")
def status():
    return jsonify(enabled=enabled(), consent_version=CONSENT_VERSION)


@app.post("/api/leads")
def leads():
    if not enabled():
        return jsonify(error="Приём заявок пока не открыт."), 503
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify(error="Недопустимый источник запроса."), 403
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return jsonify(error="Недопустимый источник запроса."), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Некорректный запрос."), 400
    if data.get("website"):
        return jsonify(error="Не удалось отправить заявку."), 400
    if data.get("consent") is not True or data.get("consent_version") != CONSENT_VERSION:
        return jsonify(error="Для заявки необходимо согласие на обработку данных."), 400
    try:
        lead_id = str(uuid.UUID(data.get("id", "")))
    except (ValueError, TypeError, AttributeError):
        return jsonify(error="Обновите страницу и повторите отправку."), 400
    fields = {}
    for key, maximum in (("name",100),("contact",150),("source",100)):
        value = data.get(key, "")
        if not isinstance(value,str) or len(value.strip()) > maximum:
            return jsonify(error="Проверьте длину заполненных полей."), 400
        fields[key] = value.strip()
    if len(fields["contact"]) < 3:
        return jsonify(error="Укажите email или телефон для ответа."), 400
    fields["id"] = lead_id
    try:
        store_lead(fields)
    except Exception:
        # Do not log contacts, connection strings or database errors.
        app.logger.error("Lead storage unavailable")
        return jsonify(error="Заявка не сохранена. Попробуйте позже."), 503
    return jsonify(ok=True, id=lead_id), 201


@app.get("/admin/leads.csv")
def export():
    token = os.environ.get("ADMIN_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if len(token) < 32 or not hmac.compare_digest(auth, "Bearer " + token):
        return jsonify(error="Доступ запрещён."), 401
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,created_at,name,contact,source,consent_version FROM moya_pribyl.leads WHERE created_at >= now() - interval '90 days' ORDER BY created_at DESC")
                rows = cur.fetchall()
    except Exception:
        return jsonify(error="Выгрузка временно недоступна."), 503
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["ID","Дата","Имя","Контакт","Источник","Версия согласия"])
    for row in rows:
        writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(("=","+","-","@")) else str(v) for v in row])
    return Response("\ufeff"+out.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=leads.csv"})
