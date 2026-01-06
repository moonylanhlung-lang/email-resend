from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import imaplib
# import smtplib
import email
import os
import base64
import pickle
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from email.header import decode_header
from email.mime.text import MIMEText

IMAP_HOST = "imap.gmail.com"
# SMTP_HOST = "smtp.gmail.com"
IMAP_PORT = 993
# SMTP_PORT = 587
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

app = Flask(__name__)

# ========== IMAP SEARCH ==========
def search_inbox_by_merchant(merchant_email):
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("INBOX")

    status, data = mail.search(
        None,
        f'(OR FROM "{merchant_email}" TO "{merchant_email}")'
    )

    email_ids = data[0].split()
    results = []

    for eid in email_ids:
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding or "utf-8", errors="ignore")

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
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    mail.logout()
    return msg["Subject"], body


# def resend_email(subject, body, merchant_email):
#     msg = MIMEText(body, "html", "utf-8")
#     msg["Subject"] = subject
#     msg["From"] = EMAIL_ACCOUNT
#     msg["To"] = merchant_email

#     server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
#     server.starttls()
#     server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
#     server.send_message(msg)
#     server.quit()
def send_gmail_api(to_email, subject, html_body):
    token_b64 = os.getenv("GMAIL_TOKEN")
    if not token_b64:
        raise Exception("GMAIL_TOKEN not set")

    creds = pickle.loads(base64.b64decode(token_b64))

    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(html_body or "", "html", "utf-8")
    message["to"] = to_email
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()
# ========== ROUTES ==========
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    merchant_email = request.json["merchant_email"]
    emails = search_inbox_by_merchant(merchant_email)
    return jsonify(emails)


# @app.route("/resend", methods=["POST"])
# def resend():
#     email_id = request.json["email_id"]
#     merchant_email = request.json["merchant_email"]

#     subject, body = get_email_body_by_id(email_id)
#     resend_email(subject, body, merchant_email)

#     return jsonify({"status": "success"})
@app.route("/resend", methods=["POST"])
def resend():
    try:
        email_id = request.json["email_id"]
        merchant_email = request.json["merchant_email"]

        subject, body = get_email_body_by_id(email_id)

        send_gmail_api(
            to_email=merchant_email,
            subject=subject,
            html_body=body
        )

        return jsonify({"status": "success"})
    except Exception as e:
        print("❌ RESEND ERROR:", str(e))
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)