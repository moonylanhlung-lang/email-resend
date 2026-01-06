from flask import Flask, render_template, request, jsonify
import imaplib
import email
import os
import base64
import pickle

from googleapiclient.discovery import build
from email.header import decode_header
from email.mime.text import MIMEText

# ================= CONFIG =================
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")  # App Password
GMAIL_TOKEN_B64 = os.getenv("GMAIL_TOKEN")

app = Flask(__name__)

# ================= IMAP =================
def search_inbox_by_merchant(merchant_email):
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("INBOX")

    status, data = mail.search(
        None,
        f'(OR FROM "{merchant_email}" TO "{merchant_email}")'
    )

    results = []

    for eid in data[0].split():
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        subject, encoding = decode_header(msg.get("Subject", ""))[0]
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
                break
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    mail.logout()
    return msg.get("Subject", ""), body


# ================= GMAIL API SEND =================
def send_gmail_api(to_email, subject, html_body):
    if not GMAIL_TOKEN_B64:
        raise Exception("GMAIL_TOKEN not set")

    creds = pickle.loads(base64.b64decode(GMAIL_TOKEN_B64))
    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(html_body or "", "html", "utf-8")
    message["to"] = to_email
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()


# ================= ROUTES =================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    merchant_email = request.form.get("merchant_email")
    emails = search_inbox_by_merchant(merchant_email)
    return jsonify(emails)


@app.route("/resend", methods=["POST"])
def resend():
    try:
        email_id = request.form.get("email_id")
        merchant_email = request.form.get("merchant_email")

        subject, body = get_email_body_by_id(email_id)
        send_gmail_api(merchant_email, subject, body)

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)