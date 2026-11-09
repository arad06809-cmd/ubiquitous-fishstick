# NEXO

نسخه تک‌فایلی پیام‌رسان NEXO بر پایه Flask و SQLite، آماده استقرار اولیه روی Render.

## فایل‌ها

- `app.py`: کد کامل برنامه
- `requirements.txt`: وابستگی‌های Python
- `render.yaml`: تنظیمات سرویس Render
- `.gitignore`: جلوگیری از ثبت فایل‌های خصوصی و داده‌های runtime
- `.env.example`: نمونه متغیرهای محیطی؛ کلید واقعی را اینجا ننویسید

## استقرار روی Render

1. همه فایل‌های این مخزن را در ریشه یک GitHub repository قرار دهید.
2. در Render گزینه **New + → Blueprint** را انتخاب و مخزن را متصل کنید.
3. Render تنظیمات `render.yaml` را می‌خواند و سرویس را می‌سازد.
4. در بخش Environment سرویس، در صورت نیاز `NEXO_OPENROUTER_KEY` را به‌عنوان Secret وارد کنید.
5. پس از Deploy، آدرس سرویس را باز کنید.

Build command: `pip install -r requirements.txt`

Start command: `gunicorn --worker-class gthread --workers 1 --threads 100 --bind 0.0.0.0:$PORT app:app`

## مالک اولیه

اولین حسابی که ثبت‌نام کند نقش مالک می‌گیرد؛ بنابراین قبل از معرفی عمومی سرویس، خودتان اولین حساب را بسازید. همچنین می‌توانید `NEXO_ADMIN` را در Environment تنظیم کنید تا نام کاربری مشخص‌شده هنگام ثبت‌نام نقش مالک بگیرد.

## ماندگاری اطلاعات

سرویس رایگان Render دیسک پایدار ندارد؛ در نتیجه SQLite، فایل‌های آپلودی و کلید نشست ممکن است پس از restart یا deploy از بین بروند. برای استفاده واقعی، یک Persistent Disk در Render بسازید، مثلاً با مسیر اتصال `/var/data`، سپس متغیر `NEXO_DATA_DIR=/var/data` را تنظیم کنید. Persistent Disk ممکن است نیازمند پلن پولی باشد.

## اجرای محلی

Python 3.12 توصیه می‌شود:

```bash
pip install -r requirements.txt
python app.py
```

سپس `http://127.0.0.1:5000` را باز کنید.

## نکات امنیتی

- کلید OpenRouter، رمزها، فایل `.nexo_secret`، دیتابیس و فایل‌های کاربران را در GitHub قرار ندهید.
- `NEXO_SECURE_COOKIES=true` برای HTTPS مناسب است؛ تنظیم آن در فایل Render فعال شده است.
- این بسته از نظر Syntax بررسی شده، اما پیش از انتشار عمومی باید نصب وابستگی‌ها و تست عملی ورود، ثبت‌نام، چت، آپلود و WebSocket روی خود Render انجام شود.
