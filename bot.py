import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
import json
import os
import threading
import time
import re
from pyzbar.pyzbar import decode
from PIL import Image
import io
import easyocr

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
ADMIN_CHANNEL_ID = -1004290008401 
SUPER_ADMIN_ID = "6788856373"
AUTO_APPROVE = True  # Set to False to require manual admin approval

bot = telebot.TeleBot(TOKEN)
USERS_FILE = 'users.json'

print("Initializing AI Text Recognition Model...")
reader = easyocr.Reader(['ch_sim', 'en'])
print("Model ready!")

def extract_id_name(image_bytes):
    try:
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
        
        # Crop to top-left quadrant: name is always here, watermark is top-right
        cropped = image.crop((0, 0, int(width * 0.5), int(height * 0.5)))
        
        img_byte_arr = io.BytesIO()
        cropped.save(img_byte_arr, format='JPEG')
        fast_image_bytes = img_byte_arr.getvalue()
        
        result = reader.readtext(fast_image_bytes, paragraph=False)
        
        # Sort top-to-bottom so the name (which is near the top) comes first
        result.sort(key=lambda x: x[0][0][1])
        
        # List of UI text to skip (lowercase)
        skip_words = [
            'bilibili', 'bilblli', 'bilbli', 'bilibil',
            'posted a comment', 'posted', 'comment', 'a comment',
            'shared via', 'shared', 'via',
            'total links', 'processing',
            '来自视频', '长按扫码', '查看视频', '你感兴趣', '内容都在',
            '扫码查看', '弹幕', '点赞', '投硬币', '收藏',
            'lv1', 'lv2', 'lv3', 'lv4', 'lv5', 'lv6',
            'world cup', 'group',
        ]
        
        for bbox, text, prob in result:
            text_clean = text.strip()
            if not text_clean: continue
            if len(text_clean) < 2: continue  # Skip single characters
            
            text_lower = text_clean.lower()
            
            # Skip if it matches any known UI text
            skip = False
            for sw in skip_words:
                if sw in text_lower:
                    skip = True
                    break
            if skip: continue
            
            # Remove LV badges
            for lv in ['LV1', 'LV2', 'LV3', 'LV4', 'LV5', 'LV6', 'Lv1', 'Lv2', 'Lv3', 'Lv4', 'Lv5', 'Lv6', 'LVE', 'LvE', 'lv8', 'LV8', 'LVB', 'LvB']:
                text_clean = text_clean.replace(lv, '')
            
            # Remove trailing OCR artifacts from the LV badge icon
            # The badge gets misread as 凹, @凹, 0, Q, O, etc.
            text_clean = re.sub(r'\s*[@]?[凹Q0O]\s*$', '', text_clean)
            # Also remove any trailing single random character after a space
            text_clean = re.sub(r'\s+.\s*$', '', text_clean) if len(text_clean) > 3 else text_clean
            text_clean = text_clean.strip()
            
            if not text_clean or len(text_clean) < 2: continue
            
            # This is likely the ID name! Return it.
            print(f"  OCR detected ID name: '{text_clean}' (confidence: {prob:.2f})")
            return text_clean
        
        print("  OCR: No valid ID name found in cropped area")
        return "Unknown"
    except Exception as e:
        print(f"OCR Error: {e}")
        return "Unknown"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
            for uid, info in data.items():
                if 'total_urls' not in info:
                    info['total_urls'] = 0
                if 'expires_at' not in info:
                    info['expires_at'] = None
            return data
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def parse_time(time_str):
    match = re.match(r"(\d+)([smhd])", time_str.lower())
    if not match:
        return None
    val = int(match.group(1))
    unit = match.group(2)
    if unit == 's': return val
    if unit == 'm': return val * 60
    if unit == 'h': return val * 3600
    if unit == 'd': return val * 86400
    return None

def find_user_id(users, identifier):
    identifier = str(identifier).strip()
    if identifier in users:
        return identifier
    if identifier.startswith('@'):
        username_query = identifier[1:].lower()
    else:
        username_query = identifier.lower()
        
    for uid, info in users.items():
        if info.get('username', '').lower() == username_query:
            return uid
    return None

def check_expiration(users, user_id):
    info = users.get(user_id)
    if not info: return False
    
    expires = info.get('expires_at')
    if expires and time.time() > expires:
        info['status'] = 'pending'
        info['expires_at'] = None
        save_users(users)
        return True 
    return False

# --- Admin Commands ---

@bot.message_handler(commands=['help'])
def admin_help(message):
    if str(message.from_user.id) != SUPER_ADMIN_ID:
        return
    help_text = """
<b>Admin Commands:</b>
/help - Show this message
/totalusers - Show total number of users
/users - List all users, usernames, and their total URLs decoded
/approve [username/id] - Approve a user permanently
/approve [username/id] [time] - Approve a user temporarily (e.g. 1h, 5m, 2d)
/block [username/id] - Block a user permanently
/block [username/id] [time] - Block a user temporarily (e.g. 10m, 1h)
/unapprove [username/id] - Revert user back to pending status

<i>Time formats: s (seconds), m (minutes), h (hours), d (days). E.g., 2m, 1h</i>
    """
    bot.reply_to(message, help_text, parse_mode="HTML")

@bot.message_handler(commands=['totalusers'])
def total_users(message):
    if str(message.from_user.id) != SUPER_ADMIN_ID:
        return
    users = load_users()
    bot.reply_to(message, f"Total users registered: {len(users)}")

@bot.message_handler(commands=['users'])
def list_users(message):
    if str(message.from_user.id) != SUPER_ADMIN_ID:
        return
    users = load_users()
    if not users:
        bot.reply_to(message, "No users found.")
        return
        
    text = "<b>User List:</b>\n\n"
    for uid, info in users.items():
        name = info.get('name', 'Unknown')
        username = info.get('username', 'No username')
        urls = info.get('total_urls', 0)
        status = info.get('status', 'unknown')
        text += f"Name: {name}\nUsername: @{username}\nID: <code>{uid}</code>\nTotal URLs: {urls}\nStatus: {status}\n---\n"
    
    for i in range(0, len(text), 4000):
        bot.send_message(message.chat.id, text[i:i+4000], parse_mode="HTML")

@bot.message_handler(commands=['approve', 'block', 'unapprove'])
def handle_admin_actions(message):
    if str(message.from_user.id) != SUPER_ADMIN_ID:
        return
        
    parts = message.text.split()
    command = parts[0].lower()
    
    if len(parts) < 2:
        bot.reply_to(message, f"Usage: {command} [username/id] [time]")
        return
        
    identifier = parts[1]
    time_str = parts[2] if len(parts) > 2 else None
    
    users = load_users()
    user_id = find_user_id(users, identifier)
    
    if not user_id:
        bot.reply_to(message, "User not found in database.")
        return
        
    expires_at = None
    if time_str:
        seconds = parse_time(time_str)
        if not seconds:
            bot.reply_to(message, "Invalid time format. Use s, m, h, or d.")
            return
        expires_at = time.time() + seconds
        
    if command == '/approve':
        users[user_id]['status'] = 'approved'
        action_text = "Approved"
    elif command == '/block':
        users[user_id]['status'] = 'rejected'
        action_text = "Blocked"
    elif command == '/unapprove':
        users[user_id]['status'] = 'pending'
        action_text = "Unapproved"
        expires_at = None
        
    users[user_id]['expires_at'] = expires_at
    save_users(users)
    
    expiry_msg = f" until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))}" if expires_at else " permanently"
    if command == '/unapprove': expiry_msg = ""
    
    bot.reply_to(message, f"User <code>{user_id}</code> (@{users[user_id].get('username', '')}) has been {action_text}{expiry_msg}.", parse_mode="HTML")
    
    try:
        notif = f"Your account has been {action_text.lower()}{expiry_msg}."
        if command == '/unapprove':
            notif = "Your account has been reverted to pending status."
        bot.send_message(user_id, notif)
    except:
        pass


@bot.message_handler(commands=['start'])
def handle_start(message):
    users = load_users()
    user_id = str(message.from_user.id)
    
    check_expiration(users, user_id)
    
    if user_id in users:
        status = users[user_id]['status']
        if status == 'approved':
            bot.reply_to(message, "You are already approved. Send me QR code photos!")
        elif status == 'pending':
            bot.reply_to(message, "Your approval is still pending.")
        elif status == 'rejected':
            bot.reply_to(message, "Your request was rejected.")
        return

    name = message.from_user.first_name
    if message.from_user.last_name:
        name += f" {message.from_user.last_name}"
    username = message.from_user.username or "No username"

    initial_status = 'approved' if AUTO_APPROVE else 'pending'
    users[user_id] = {
        'status': initial_status,
        'name': name,
        'username': username,
        'total_urls': 0,
        'expires_at': None
    }
    save_users(users)

    if AUTO_APPROVE:
        bot.reply_to(message, "You are approved! Send me QR code photos.")
        if ADMIN_CHANNEL_ID:
            try:
                auto_text = f"✅ Auto-Approved:\nName: {name}\nUsername: @{username}\nUser ID: <code>{user_id}</code>"
                bot.send_message(ADMIN_CHANNEL_ID, auto_text, parse_mode="HTML")
            except:
                pass
    else:
        bot.reply_to(message, "Your request has been sent to the admin for approval. Please wait.")

    if not AUTO_APPROVE and ADMIN_CHANNEL_ID:
        markup = InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(
            InlineKeyboardButton("Approve", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{user_id}")
        )
        admin_text = f"New user request:\nName: {name}\nUsername: @{username}\nUser ID: <code>{user_id}</code>"
        try:
            bot.send_message(ADMIN_CHANNEL_ID, admin_text, reply_markup=markup, parse_mode="HTML")
        except:
            pass

@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document'])
def handle_channel_post(message):
    pass
    
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_') or call.data.startswith('reject_'))
def handle_approval(call):
    action, user_id = call.data.split('_')
    users = load_users()
    
    if user_id not in users:
        bot.answer_callback_query(call.id, "User not found in database.")
        return
        
    status = 'approved' if action == 'approve' else 'rejected'
    users[user_id]['status'] = status
    users[user_id]['expires_at'] = None
    save_users(users)
    
    try:
        bot.send_message(int(user_id), f"Your request has been {status}!")
    except Exception as e:
        print(f"Could not notify user {user_id}: {e}")
        
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"{call.message.text}\n\n<b>Status: {status.upper()}</b>",
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id, f"User {status}.")

# --- Photo Batching Logic ---

# Store just the file_ids quickly. Process them AFTER format is chosen.
user_sessions = {}
session_lock = threading.Lock()

def finish_batch(user_id, chat_id):
    with session_lock:
        if user_id not in user_sessions: return
        session = user_sessions[user_id]
        images = list(session['images'])
    
    if not images: return
    
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("With Image", callback_data=f"format_with_{user_id}"),
        InlineKeyboardButton("Without Image", callback_data=f"format_without_{user_id}")
    )
    
    bot.send_message(chat_id, f"Received {len(images)} images. How would you like them formatted?", reply_markup=markup)

import concurrent.futures

def download_image(file_id):
    try:
        # We must create a new bot instance or use the global one carefully.
        # pyTelegramBotAPI is thread-safe for basic calls.
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        return downloaded_file, file_id
    except Exception as e:
        print(f"Failed to download {file_id}: {e}")
        return None, None

def process_batch(chat_id, user_id, format_type, images):
    start_time = time.time()
    processing_msg = bot.send_message(chat_id, "⚡ Downloading and processing images with AI... Please wait.")
    
    processed_images = []
    
    # 1. Download images concurrently (This is the main bottleneck)
    downloads = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        downloads = list(executor.map(download_image, images))
        
    # 2. Run AI OCR sequentially. 
    # (Running PyTorch AI models in parallel threads causes deadlocks!)
    for downloaded_file, file_id in downloads:
        if not downloaded_file:
            continue
            
        try:
            # Extract ID Name with OCR
            id_name = extract_id_name(downloaded_file)
            
            # Decode QR
            image = Image.open(io.BytesIO(downloaded_file))
            decoded_objects = decode(image)
            
            url = None
            if decoded_objects:
                url = decoded_objects[0].data.decode('utf-8')
            else:
                url = "No QR found"
                
            processed_images.append({
                'url': url,
                'id_name': id_name,
                'file_id': file_id
            })
        except Exception as e:
            print(f"Failed to process AI for {file_id}: {e}")
            
    # Group by id_name
    grouped = {}
    for img in processed_images:
        name = img['id_name']
        if name not in grouped: grouped[name] = []
        grouped[name].append(img)
        
    try:
        bot.delete_message(chat_id, processing_msg.message_id)
    except:
        pass
        
    try:
        if format_type == 'with':
            for name, imgs in grouped.items():
                for i in range(0, len(imgs), 10):
                    chunk = imgs[i:i+10]
                    media = []
                    links = []
                    for idx, img in enumerate(chunk):
                        links.append(img['url'])
                    
                    caption = "\n".join(links)
                    
                    for idx, img in enumerate(chunk):
                        if idx == 0:
                            media.append(InputMediaPhoto(img['file_id'], caption=caption))
                        else:
                            media.append(InputMediaPhoto(img['file_id']))
                            
                    bot.send_media_group(chat_id, media)
        elif format_type == 'without':
            for name, imgs in grouped.items():
                text = f"{name}\n\n"
                for img in imgs:
                    text += f"{img['url']}\n"
                bot.send_message(chat_id, text)
                
        # Update stats
        users = load_users()
        if user_id in users:
            users[user_id]['total_urls'] = users[user_id].get('total_urls', 0) + len(processed_images)
            save_users(users)
        
        # Summary message
        elapsed = time.time() - start_time
        if elapsed >= 60:
            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        else:
            time_str = f"{elapsed:.1f}s"
        
        format_name = "With Image" if format_type == 'with' else "Without Image"
        
        summary = f"✅ Done\n\n"
        summary += f"📷 Total Images: {len(processed_images)}\n"
        summary += f"📝 Format: {format_name}\n"
        summary += f"⏱ Time Taken: {time_str}"
        
        bot.send_message(chat_id, summary)
            
    except Exception as e:
        bot.send_message(chat_id, f"Error formatting batch: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('format_'))
def handle_format_choice(call):
    parts = call.data.split('_')
    format_type = parts[1]
    user_id = parts[2]
    
    if str(call.from_user.id) != user_id:
        bot.answer_callback_query(call.id, "This is not your batch.")
        return
        
    with session_lock:
        if user_id not in user_sessions or not user_sessions[user_id]['images']:
            bot.answer_callback_query(call.id, "Batch expired or already processed.")
            return
        images = list(user_sessions[user_id]['images'])
        user_sessions[user_id]['images'] = [] # clear immediately
        
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Processing...")
    
    # Run processing in a separate thread so bot stays responsive
    threading.Thread(target=process_batch, args=(call.message.chat.id, user_id, format_type, images)).start()

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    users = load_users()
    user_id = str(message.from_user.id)
    
    check_expiration(users, user_id)
    
    if user_id not in users or users[user_id]['status'] != 'approved':
        bot.reply_to(message, "You are not approved to use this bot.")
        return
        
    # Just grab the file_id super fast!
    file_id = message.photo[-1].file_id
    
    with session_lock:
        if user_id not in user_sessions:
            user_sessions[user_id] = {'timer': None, 'images': []}
            
        session = user_sessions[user_id]
        
        if session['timer']:
            session['timer'].cancel()
            
        session['images'].append(file_id)
        
        # 1.5 seconds delay allows all images in a bulk forward to arrive without making the user wait
        session['timer'] = threading.Timer(1.5, finish_batch, args=(user_id, message.chat.id))
        session['timer'].start()

if __name__ == '__main__':
    print("Bot is starting...")
    bot.infinity_polling()
      
