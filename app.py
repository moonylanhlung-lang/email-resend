from flask import Flask, render_template, request, jsonify, redirect, session
import imaplib, email, os, base64, pickle, json, datetime
import requests

from googleapiclient.discovery import build
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

# ================= CONFIG =================
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
GMAIL_TOKEN = os.getenv("GMAIL_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY", "railway-secret")

if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
    raise ValueError("EMAIL_ACCOUNT and EMAIL_PASSWORD environment variables must be set")

if not GMAIL_TOKEN:
    raise ValueError("GMAIL_TOKEN environment variable must be set")

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
    try:
        if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
            raise ValueError("IMAP credentials not configured")
        
        app.logger.info("Connecting to IMAP...")
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")
        app.logger.info("IMAP connected successfully")
        
        app.logger.info(f"Searching emails for: {merchant_email}")
        _, data = mail.search(None, f'(OR FROM "{merchant_email}" TO "{merchant_email}")')
        app.logger.info(f"Search returned {len(data[0].split())} email IDs")
        
        results = []

        for eid in data[0].split():
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject, enc = decode_header(msg.get("Subject", ""))[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(enc or "utf-8", errors="ignore")

                date_str = msg.get("Date")
                parsed_date = None
                if date_str:
                    try:
                        parsed_date = parsedate_to_datetime(date_str)
                    except:
                        parsed_date = datetime.datetime.now()  # fallback
                else:
                    parsed_date = datetime.datetime.now()  # fallback

                results.append({
                    "id": eid.decode(),
                    "subject": subject,
                    "from": msg.get("From"),
                    "date": date_str or "Unknown",
                    "parsed_date": parsed_date
                })
            except Exception as e:
                app.logger.warning(f"Error processing email {eid}: {str(e)}")
                continue

        # Sort by parsed_date descending (newest first)
        results.sort(key=lambda x: x["parsed_date"], reverse=True)

        mail.logout()
        app.logger.info(f"Returning {len(results)} processed emails")
        return results
    except Exception as e:
        app.logger.error(f"Error in search_inbox_by_merchant: {str(e)}", exc_info=True)
        raise ValueError(f"Failed to search emails: {str(e)}")

def get_email_body_by_id(email_id):
    try:
        if not EMAIL_ACCOUNT or not EMAIL_PASSWORD:
            raise ValueError("IMAP credentials not configured")
        
        app.logger.info(f"Fetching email body for ID: {email_id}")
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")

        _, msg_data = mail.fetch(email_id.encode(), "(RFC822)")
        if not msg_data or not msg_data[0] or not msg_data[0][1]:
            raise ValueError("Email not found or empty")
        msg = email.message_from_bytes(msg_data[0][1])
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="ignore")
                    break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="ignore")

        mail.logout()
        app.logger.info(f"Email body fetched, length: {len(body)}")
        return msg.get("Subject", ""), body
    except Exception as e:
        app.logger.error(f"Error in get_email_body_by_id: {str(e)}", exc_info=True)
        raise ValueError(f"Failed to get email body: {str(e)}")

# ================= GMAIL API =================
def send_gmail_api(to_email, subject, html_body):
    try:
        if not html_body or not html_body.strip():
            raise ValueError("Email body is empty")
        
        app.logger.info("Decoding Gmail credentials...")
        try:
            creds = pickle.loads(base64.b64decode(GMAIL_TOKEN))
        except Exception as e:
            raise ValueError(f"Invalid GMAIL_TOKEN: {str(e)}")
        
        app.logger.info("Building Gmail service...")
        service = build("gmail", "v1", credentials=creds)

        msg = MIMEText(html_body, "html", "utf-8")
        msg["to"] = to_email
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        app.logger.info(f"Sending email to {to_email} with subject: {subject}")
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        app.logger.info("Email sent successfully")
        return result
    except Exception as e:
        app.logger.error(f"Error in send_gmail_api: {str(e)}", exc_info=True)
        raise ValueError(f"Failed to send email via Gmail API: {str(e)}")

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
    
    try:
        merchant_email = request.form["merchant_email"]
        app.logger.info(f"Searching emails for: {merchant_email}")
        
        results = search_inbox_by_merchant(merchant_email)
        app.logger.info(f"Found {len(results)} emails")
        
        resend_status = {"auto_resend": False, "resend_email": None}
        
        if results:
            # Auto resend the latest email
            latest_email = results[0]
            app.logger.info(f"Auto resending latest email: {latest_email['subject']}")
            
            try:
                subject, body = get_email_body_by_id(latest_email["id"])
                app.logger.info(f"Got email body, length: {len(body)}")
                
                send_gmail_api(merchant_email, subject, body)
                app.logger.info("Email sent successfully")
                
                write_log({
                    "time": datetime.datetime.utcnow().isoformat(),
                    "user": session["user"]["username"],
                    "merchant_email": merchant_email,
                    "subject": subject,
                    "action": "auto_resend_latest"
                })
                
                resend_status = {"auto_resend": True, "resend_email": {"id": latest_email["id"], "subject": subject}}
            except ValueError as e:
                app.logger.error(f"Resend failed: {str(e)}")
                resend_status = {"auto_resend": False, "error": str(e)}
                write_log({
                    "time": datetime.datetime.utcnow().isoformat(),
                    "user": session["user"]["username"],
                    "merchant_email": merchant_email,
                    "action": "auto_resend_failed",
                    "error": str(e)
                })
            except Exception as e:
                app.logger.error(f"Unexpected resend error: {str(e)}")
                resend_status = {"auto_resend": False, "error": f"Unexpected error: {str(e)}"}
                write_log({
                    "time": datetime.datetime.utcnow().isoformat(),
                    "user": session["user"]["username"],
                    "merchant_email": merchant_email,
                    "action": "auto_resend_failed",
                    "error": str(e)
                })
        
        return jsonify({"emails": results, "resend_status": resend_status})
    except Exception as e:
        app.logger.error(f"Error in /search: {str(e)}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route("/resend", methods=["POST"])
def resend():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401

    email_id = request.form["email_id"]
    merchant_email = request.form["merchant_email"]
    
    try:
        subject, body = get_email_body_by_id(email_id)
        send_gmail_api(merchant_email, subject, body)

        write_log({
            "time": datetime.datetime.utcnow().isoformat(),
            "user": session["user"]["username"],
            "merchant_email": merchant_email,
            "subject": subject
        })

        return jsonify({"status": "success"})
    except ValueError as e:
        write_log({
            "time": datetime.datetime.utcnow().isoformat(),
            "user": session["user"]["username"],
            "merchant_email": merchant_email,
            "action": "resend_failed",
            "error": str(e)
        })
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        write_log({
            "time": datetime.datetime.utcnow().isoformat(),
            "user": session["user"]["username"],
            "merchant_email": merchant_email,
            "action": "resend_failed",
            "error": str(e)
        })
        return jsonify({"status": "error", "message": f"Unexpected error: {str(e)}"}), 500

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