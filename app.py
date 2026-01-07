from flask import Flask, render_template, request, jsonify, redirect, session
import imaplib, email, os, base64, pickle, json, datetime
import requests

from googleapiclient.discovery import build
from email.header import decode_header
from email.mime.text import MIMEText

# ================= CONFIG =================
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
GMAIL_TOKEN = os.getenv("GMAIL_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY", "railway-secret")

LOGIN_API = "https://cnps.vn00.vn.fastgo.cloud:9803/login"
LOG_FILE = "logs.json"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ================= UTILS =================
def login_required():
    return "user" in session

def write_log(data):
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    logs.append(data)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

# ================= LOGIN =================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username")
    password = request.form.get("password")

    resp = requests.post(
        LOGIN_API,
        json={"username": username, "password": password},
        headers={"content-type": "application/json"},
        timeout=10
    )

    data = resp.json()
    if data.get("success"):
        session["user"] = {
            "username": data["data"]["username"],
            "name": data["data"]["name"],
            "token": data["data"]["token"]
        }
        return redirect("/")
    return render_template("login.html", error="Sai tài khoản hoặc mật khẩu")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ================= IMAP =================
def search_inbox_by_merchant(merchant_email):
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("INBOX")

    _, data = mail.search(None, f'(OR FROM "{merchant_email}" TO "{merchant_email}")')
    results = []

    for eid in data[0].split():
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        subject, enc = decode_header(msg.get("Subject", ""))[0]
        if isinstance(subject, bytes):
            subject = subject.decode(enc or "utf-8", errors="ignore")

        results.append({
            "id": eid.decode(),
            "subject": subject,
            "from": msg.get("From"),
            "date": msg.get("Date")
        })

    mail.logout()
    return results

def get_email_body_by_id(email_id):
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("INBOX")

    _, msg_data = mail.fetch(email_id.encode(), "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    mail.logout()
    return msg.get("Subject", ""), body

# ================= GMAIL API =================
def send_gmail_api(to_email, subject, html_body):
    creds = pickle.loads(base64.b64decode(GMAIL_TOKEN))
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEText(html_body, "html", "utf-8")
    msg["to"] = to_email
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

# ================= ROUTES =================
@app.route("/")
def index():
    if not login_required():
        return redirect("/login")
    return render_template("index.html", user=session["user"])

@app.route("/search", methods=["POST"])
def search():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(search_inbox_by_merchant(request.form["merchant_email"]))

@app.route("/resend", methods=["POST"])
def resend():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401

    email_id = request.form["email_id"]
    merchant_email = request.form["merchant_email"]
    subject, body = get_email_body_by_id(email_id)
    send_gmail_api(merchant_email, subject, body)

    write_log({
        "time": datetime.datetime.utcnow().isoformat(),
        "user": session["user"]["username"],
        "merchant_email": merchant_email,
        "subject": subject
    })

    return jsonify({"status": "success"})

@app.route("/logs")
def logs():
    if not login_required():
        return redirect("/login")
    if not os.path.exists(LOG_FILE):
        return jsonify([])
    with open(LOG_FILE) as f:
        return jsonify(json.load(f))

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)