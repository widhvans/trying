import telebot
from telebot import types
import qrcode
import hashlib
import hmac
import base64
import json
import requests
from config import TELEGRAM_TOKEN, PAYTM_MID, PAYTM_MERCHANT_KEY, PAYTM_UPI_ID
import sqlite3
import time
import os

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('payments.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS payments
                 (order_id TEXT PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT)''')
    conn.commit()
    conn.close()

# Generate checksum for Paytm API
def generate_checksum(data, merchant_key):
    data_str = json.dumps(data, separators=(',', ':'))
    checksum = hmac.new(
        key=merchant_key.encode('utf-8'),
        msg=data_str.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(checksum).decode('utf-8')

# Generate UPI QR code
def generate_upi_qr(upi_id, amount, order_id):
    upi_url = f"upi://pay?pa={upi_id}&pn=Merchant&am={amount}&tn=Order_{order_id}&cu=INR"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(upi_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill='black', back_color='white')
    qr_img.save(f"qr_{order_id}.png")
    return f"qr_{order_id}.png"

# Start command
@bot.message_handler(commands=['start'])
def start(message):
    init_db()
    user_id = message.chat.id
    order_id = f"ORDER_{user_id}_{int(time.time())}"
    amount = 150.0

    # Save order to database
    conn = sqlite3.connect('payments.db')
    c = conn.cursor()
    c.execute("INSERT INTO payments (order_id, user_id, amount, status) VALUES (?, ?, ?, ?)",
              (order_id, user_id, amount, 'PENDING'))
    conn.commit()
    conn.close()

    # Generate QR code
    qr_path = generate_upi_qr(PAYTM_UPI_ID, amount, order_id)
    
    # Create "Check Payment namn" button
    keyboard = types.InlineKeyboardMarkup()
    check_button = types.InlineKeyboardButton(text="Check Payment", callback_data=f"check_{order_id}")
    keyboard.add(check_button)

    # Send QR code and button
    with open(qr_path, 'rb') as qr_file:
        bot.send_photo(
            user_id,
            qr_file,
            caption=f"Scan this QR to pay ₹{amount} for Order ID: {order_id}\nClick below to verify payment.",
            reply_markup=keyboard
        )
    os.remove(qr_path)  # Clean up QR file

# Check payment status
@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    order_id = call.data.split('_')[1]
    user_id = call.message.chat.id

    # Fetch order from database
    conn = sqlite3.connect('payments.db')
    c = conn.cursor()
    c.execute("SELECT amount, status FROM payments WHERE order_id = ?", (order_id,))
    result = c.fetchone()
    conn.close()

    if not result:
        bot.answer_callback_query(call.id, "Order not found.")
        return

    amount, status = result

    if status == 'SUCCESS':
        bot.answer_callback_query(call.id, "Payment already verified!")
        bot.send_message(user_id, f"Payment for Order ID: {order_id} is successful!")
        return

    # Paytm Transaction Status API call
    payload = {
        "body": {"mid": PAYTM_MID, "orderId": order_id},
        "head": {"version": "v1"}
    }
    checksum = generate_checksum(payload['body'], PAYTM_MERCHANT_KEY)
    payload['head']['signature'] = checksum

    try:
        response = requests.post(
            'https://securegw.paytm.in/v3/order/status',
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        response_data = response.json()

        if response_data['body']['resultInfo']['resultStatus'] == 'TXN_SUCCESS':
            # Update database
            conn = sqlite3.connect('payments.db')
            c = conn.cursor()
            c.execute("UPDATE payments SET status = ? WHERE order_id = ?", ('SUCCESS', order_id))
            conn.commit()
            conn.close()

            bot.answer_callback_query(call.id, "Payment verified!")
            bot.send_message(user_id, f"Payment for Order ID: {order_id} is successful! Amount: ₹{amount}")
        else:
            bot.answer_callback_query(call.id, "Payment not received yet.")
            bot.send_message(user_id, f"Payment for Order ID: {order_id} is still pending.")
    except Exception as e:
        bot.answer_callback_query(call.id, "Error checking payment.")
        bot.send_message(user_id, f"Error verifying payment: {str(e)}")

# Start polling
if __name__ == '__main__':
    bot.polling(none_stop=True)
