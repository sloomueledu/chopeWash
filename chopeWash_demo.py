# CDE2300 Product Design and Innovation - Group 6 
# Developer: Samuel Loo
# Project: chopeWash (Unified Architecture)
# Architecture: Dual Hybrid (Timeslots + Smart FCFS) with Intent-Driven Balancing & Hardware Tracking
import os
from dotenv import load_dotenv
import asyncio
import ssl
import paho.mqtt.client as mqtt
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.error import BadRequest, Forbidden

# Error Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
load_dotenv()

class chopeWashBot:
    def __init__(self, token):
        # --- First-Come-First-Serve (FCFS) Queuing ---
        self.washer_fcfs_queue = [] 
        self.dryer_fcfs_queue = []
        self.paused_users = {} # Tracks paused FCFS users
        self.pause_cooldowns = {}

        # --- Timeslot booking ---
        self.timeslot_bookings = {} # format: {"2026-10-04 14:00": {"user": 123, "intent": "Wash & Dry", "dry_time": 30, "started": False}}
        
        # --- Cycle Tracking ---
        self.washer_active_cycles = {} 
        self.dryer_active_cycles = {}
        self.washer_pending_collection = {} 
        self.dryer_pending_collection = {}
        self.dryer_pending_start = [] # For VIP Timeslot users moving from washer to dryer

        # --- Soft Bans Tracking ---
        self.soft_bans_bookings = [] 
        self.soft_bans_fcfs_queue = []
        self.user_strikes = {} # Format: {user_id: strike_count}
        self.turn_grace_period = 30 # Live: 15 minutes (900s) to claim an open machine

        # --- Machine Tracking ---
        # 0 - Washer A | 1 - Washer B | 2 - Dryer A | 3 - Dryer B
        self.available_machines = [0, 1, 2, 3]

        # --- System Settings (Demo Testing Config) ---
        self.grace_period = 30 # Live: 15 minutes
        self.machine_forgot_ban = 48 # Hours
        self.forgot_washer_and_dryer = 72 # Hours
        self.missed_queue_ban = 2 # Hours
        self.cycle_timer = 5 # Default Wash Time: 35 minutes (2100s)
        self.bugs_URL = "https://forms.gle/vxBhDgbCQDFo4ULL6"
        self.feedback_URL = "https://forms.gle/opxBXDSSYMTa8qdg9"

        # --- Telegram App Setup ---
        self.app = (
            ApplicationBuilder()
            .token(token)
            .post_init(self.start_background_tasks) # <-- Add this line back
            .build()
        )
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CallbackQueryHandler(self.button_handler))

    # --- MQTT & Background Threads ---
        self.telegram_bot = None  
        self.async_loop = None    
        self.mqtt_queue = asyncio.Queue()
        
        self.mqtt_broker = "io.adafruit.com"
        self.mqtt_port = 8883
        self.mqtt_user = os.getenv("ADAFRUIT_IO_USERNAME")
        self.mqtt_password = os.getenv("ADAFRUIT_IO_KEY")
        
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_password)
        self.mqtt_client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

    # ==========================================
    # MQTT NETWORK LAYER (TWO-WAY)
    # ==========================================
    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("Connected to Adafruit IO for two-way MQTT!")
            client.subscribe(f"{self.mqtt_user}/feeds/chopewash-feedback")
        else:
            logging.error(f"Failed to connect to MQTT, return code {reason_code}")

    def on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        logging.info(f"ESP32 Feedback Received: {payload}")
        try:
            if ":" in payload:
                user_id_str, text_msg = payload.split(":", 1)
                user_id = int(user_id_str)
                if self.telegram_bot and self.async_loop:
                    coroutine = self.telegram_bot.send_message(chat_id=user_id, text=f"🤖 **Hardware Update:**\n{text_msg}", parse_mode="Markdown")
                    asyncio.run_coroutine_threadsafe(coroutine, self.async_loop)
        except Exception as e:
            logging.error(f"Error parsing ESP32 feedback: {e}")

    async def start_background_tasks(self, application):
        self.telegram_bot = application.bot
        self.async_loop = asyncio.get_running_loop()
        self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
        self.mqtt_client.loop_start() 
        asyncio.create_task(self.mqtt_worker())
        logging.info("MQTT Worker & Listener started.")

    async def mqtt_worker(self):
        while True:
            topic, payload = await self.mqtt_queue.get()
            try:
                self.mqtt_client.publish(topic, payload)
            except Exception as e:
                logging.error(f"Failed to publish: {e}")
            self.mqtt_queue.task_done()
            await asyncio.sleep(1.1)

    async def trigger_switchbot_mqtt(self, machine_id: int, user_id: int):
        feed_map = {0: "washerA", 1: "washerB", 2: "dryerA", 3: "dryerB"}
        feed_name = feed_map.get(machine_id)
        if not feed_name: return False
        topic = f"{self.mqtt_user}/feeds/{feed_name}"
        await self.mqtt_queue.put((topic, str(user_id)))
        return True
    
    def runbot(self):
        print('✅ chopeWash System Booted & Listening...')
        self.app.run_polling()

    # ==========================================
    # HELPERS & DASHBOARD UI GENERATOR
    # ==========================================
    def is_user_banned(self, user_id: int) -> bool:
        now = datetime.now()
        
        # Check FCFS bans
        active_fcfs_ban = next((b for b in self.soft_bans_fcfs_queue if b["id"] == user_id), None)
        if active_fcfs_ban:
            if now < active_fcfs_ban["expires"]:
                return True
            self.soft_bans_fcfs_queue.remove(active_fcfs_ban)

        # Check Timeslot bans
        active_booking_ban = next((b for b in self.soft_bans_bookings if b["id"] == user_id), None)
        if active_booking_ban:
            if now < active_booking_ban["expires"]:
                return True
            self.soft_bans_bookings.remove(active_booking_ban)

        return False
    
    def has_active_booking(self, user_id: int) -> bool:
        """Checks if a user already has an active, unstarted booking."""
        now = datetime.now()
        for slot_str, booking_data in self.timeslot_bookings.items():
            if booking_data["user"] == user_id and not booking_data.get("started"):
                try:
                    slot_time = datetime.strptime(slot_str, "%Y-%m-%d %H:%M")
                    if slot_time + timedelta(minutes=15) > now:
                        return True
                except ValueError:
                    pass
        return False

    def get_dashboard_ui(self, user_id: int):
        now = datetime.now()
        text = "👋🏻 **Welcome to chopeWash!**\n\n"
        
        buttons = [
            [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="view_status")],
            [InlineKeyboardButton("📅 Book Advance Timeslot", callback_data="flow_book")],
            [InlineKeyboardButton("🏃‍♂️ Join Live Queue (FCFS)", callback_data="flow_fcfs")],
            [InlineKeyboardButton("⚠️ Report Issue / Feedback", url=self.bugs_URL)],
            [InlineKeyboardButton("💬 Help Us Understand Your Needs!", url=self.feedback_URL)]
        ]

        # --- 1. HARDWARE AVAILABILITY & LOOK-AHEAD CHECK ---
        # A. User-Specific Lockouts (Used to render their personal Start buttons)
        upcoming_bookings = sum(1 for time_str, d in self.timeslot_bookings.items() if d["user"] != user_id and not d.get("started") and 0 < (datetime.strptime(time_str, "%Y-%m-%d %H:%M") - now).total_seconds() / 60.0 <= 60)
        vip_dryer_waitlist = sum(1 for u in self.dryer_pending_start if u["id"] != user_id)
        
        # B. Global Lockouts (Used for the public UI text)
        total_upcoming_bookings = sum(1 for time_str, d in self.timeslot_bookings.items() if not d.get("started") and 0 < (datetime.strptime(time_str, "%Y-%m-%d %H:%M") - now).total_seconds() / 60.0 <= 60)
        total_vip_dryer_waitlist = len(self.dryer_pending_start)
        
        free_washers = [m for m in self.available_machines if m in [0, 1]]
        free_dryers = [m for m in self.available_machines if m in [2, 3]]
        
        # C. Calculate Effective Queue Machines (Physical Free - Locked VIPs)
        queue_washers_avail = max(0, len(free_washers) - total_upcoming_bookings)
        queue_dryers_avail = max(0, len(free_dryers) - total_vip_dryer_waitlist)

        is_running = user_id in self.washer_active_cycles or user_id in self.dryer_active_cycles
        booking_start = False
        
        fcfs_washer_ready = len(free_washers) > upcoming_bookings
        fcfs_dryer_ready = len(free_dryers) > vip_dryer_waitlist

        # --- 2. CYCLE STATUS ---
        text += "**⚙️ Cycle Status:**\n"
        if user_id in self.washer_active_cycles:
            cycle = self.washer_active_cycles[user_id]
            rem_min = max(0, int((cycle["end_time"] - now).total_seconds() // 60))
            text += f"• **WASHER {'A' if cycle['machine_id'] == 0 else 'B'} RUNNING:**\n  Intent: {cycle['intent']}\n  Time Remaining: ~{rem_min} mins\n\n"
        elif user_id in self.dryer_active_cycles:
            cycle = self.dryer_active_cycles[user_id] 
            rem_min = max(0, int((cycle["end_time"] - now).total_seconds() // 60))
            text += f"• **DRYER {'A' if cycle['machine_id'] == 2 else 'B'} RUNNING:**\n  Intent: {cycle['intent']}\n  Time Remaining: ~{rem_min} mins\n\n"
        else:
            text += "• _No active cycle detected._\n\n"

        # --- 3. PENDING COLLECTION ---
        if user_id in self.washer_pending_collection:
            pending = self.washer_pending_collection[user_id]
            machine_id = "A" if pending["machine_id"] == 0 else "B"
            rem_sec = max(0, int((pending["expiry_time"] - now).total_seconds()))
            rem_min, rem_s = divmod(rem_sec, 60)
            
            text += f"⚠️ **ACTION REQUIRED:**\nWasher {machine_id} cycle complete. Please collect your clothes!\n⏳ **Time left to claim:** {rem_min}m {rem_s}s\n\n"
            buttons.insert(0, [InlineKeyboardButton("✅ I have collected my clothes!", callback_data="collected_washer")])
            
        elif user_id in self.dryer_pending_collection: 
            pending = self.dryer_pending_collection[user_id]
            machine_id = "A" if pending["machine_id"] == 2 else "B" 
            rem_sec = max(0, int((pending["expiry_time"] - now).total_seconds()))
            rem_min, rem_s = divmod(rem_sec, 60)
            
            text += f"⚠️ **ACTION REQUIRED:**\nDryer {machine_id} cycle complete. Please collect your clothes!\n⏳ **Time left to claim:** {rem_min}m {rem_s}s\n\n"
            buttons.insert(0, [InlineKeyboardButton("✅ I have collected my clothes!", callback_data="collected_dryer")])

        # --- 4. BOOKING STATUS ---
        text += f"📅 **Upcoming Laundry Booking:**\n"
        user_bookings = [(s, d) for s, d in self.timeslot_bookings.items() if d["user"] == user_id and not d.get("started")]
        
        if user_bookings:
            for slot, d in sorted(user_bookings, key=lambda x: x[0]):
                dry_info = f"{d['dry_time']}m dry" if d['dry_time'] > 0 else "No dry"
                text += f"• `{slot}` ({d['intent']} — {dry_info})\n"
                buttons.append([InlineKeyboardButton(f"❌ Cancel {slot[-5:]}", callback_data=f"cancel_book_{slot}")])
                
                try:
                    slot_time = datetime.strptime(slot, "%Y-%m-%d %H:%M")
                    if abs((now - slot_time).total_seconds()) <= 900:
                        if len(free_washers) > 0:
                            assigned = "A" if free_washers[0] == 0 else "B"
                            text += f"  🟢 **Ready!** Head down to **Washer {assigned}** now.\n"
                            booking_start = True
                        else:
                            text += "  ⏳ **Starting soon.** Waiting for a machine to clear...\n"
                except ValueError:
                    pass
        else:
            text += "• _No upcoming reservations._\n\n"

        # --- 5. QUEUING STATUS ---
        text += "**🏃‍♂️ Queuing Status:**\n"
        w_queue_entry = next((item for item in self.washer_fcfs_queue if item["id"] == user_id), None)
        d_queue_entry = next((item for item in self.dryer_fcfs_queue if item["id"] == user_id), None)
        
        if w_queue_entry:
            pos = self.washer_fcfs_queue.index(w_queue_entry) + 1
            text += f"• **Washer Queue:** #{pos} of {len(self.washer_fcfs_queue)}\n"
            buttons.append([InlineKeyboardButton("⏸ Pause Spot", callback_data="fcfs_pause")])
            buttons.append([InlineKeyboardButton("🚪 Leave Line", callback_data="fcfs_leave")])
            if pos == 1: 
                if fcfs_washer_ready:
                    # FIX: Change free_washers[-1] to free_washers[0]
                    assigned = "A" if free_washers[0] == 0 else "B"
                    text += f"  🟢 **It's your turn!** Head to **Washer {assigned}**.\n"
                    
                    if "turn_expiry" in w_queue_entry:
                        rem_sec = max(0, int((w_queue_entry["turn_expiry"] - now).total_seconds()))
                        rem_min, rem_s = divmod(rem_sec, 60)
                        text += f"  ⏳ **Time to start:** {rem_min}m {rem_s}s\n"

                    booking_start = True 
                else:
                    text += "  ⏳ **You are next!** Waiting for a washer to clear (upcoming reservations have priority)...\n"
                    
        elif d_queue_entry:
            pos = self.dryer_fcfs_queue.index(d_queue_entry) + 1
            text += f"• **Dryer Queue:** #{pos} of {len(self.dryer_fcfs_queue)}\n"
            buttons.append([InlineKeyboardButton("⏸ Pause Spot", callback_data="fcfs_pause")])
            buttons.append([InlineKeyboardButton("🚪 Leave Line", callback_data="fcfs_leave")])
            if pos == 1:
                if fcfs_dryer_ready:
                    # FIX: Change free_dryers[-1] to free_dryers[0]
                    assigned = "A" if free_dryers[0] == 2 else "B"
                    text += f"  🟢 **It's your turn!** Head to **Dryer {assigned}**.\n"
                    
                    if "turn_expiry" in d_queue_entry:
                        rem_sec = max(0, int((d_queue_entry["turn_expiry"] - now).total_seconds()))
                        rem_min, rem_s = divmod(rem_sec, 60)
                        text += f"  ⏳ **Time to start:** {rem_min}m {rem_s}s\n"

                    booking_start = True
                else:
                    text += "  ⏳ **You are next!** Waiting for a dryer to clear (VIP transition has priority)...\n"

        elif user_id in self.paused_users:
            rem_mins = max(0, int((self.paused_users[user_id]["expires"] - now).total_seconds() // 60))
            text += f"• **State:** ⏸ Paused ({rem_mins}m remaining)\n"
            buttons.append([InlineKeyboardButton("▶️ Resume Line", callback_data="fcfs_resume")])
        else:
            text += "• _Not currently in any live queue._\n"

        # --- 6. VIP DRYER TRANSITION ---
        in_dryer_transition = next((u for u in self.dryer_pending_start if u["id"] == user_id), None)
        if in_dryer_transition:
            if len(free_dryers) > 0:
                assigned = "A" if free_dryers[0] == 2 else "B"
                text += f"\n🟢 **Reserved Dryer {assigned} is waiting for you.** Please transfer ASAP.\n"
                booking_start = True
            else:
                text += "\n⏳ **Waiting for your reserved dryer to clear...**\n"

        # --- 7. HARDWARE START BUTTONS ---
        text += f"\n🔧 **Queue Hardware Availability:**\n"
        text += f"• Washers Available: {queue_washers_avail} | Dryers Available: {queue_dryers_avail}\n"

        is_banned = self.is_user_banned(user_id)

        if is_banned and not in_dryer_transition:
            text += "\n🚫 **Account Suspended:** You are currently banned from starting new laundry."
        elif booking_start and not is_running:
            if (user_bookings and len(free_washers) > 0) or (w_queue_entry and fcfs_washer_ready):
                buttons.insert(0, [InlineKeyboardButton("▶️ START WASHER NOW", callback_data="start_washer")])
            elif (in_dryer_transition and len(free_dryers) > 0) or (d_queue_entry and fcfs_dryer_ready):
                buttons.insert(0, [InlineKeyboardButton("▶️ START DRYER NOW", callback_data="start_dryer")])
        
        return text, InlineKeyboardMarkup(buttons)
   
    async def issue_strike(self, context, user_id: int, reason: str):
        """Helper to issue a strike and apply a 48-hour ban on the 3rd strike."""
        strikes = self.user_strikes.get(user_id, 0) + 1
        self.user_strikes[user_id] = strikes
        
        if strikes >= 3:
            ban_expiry = datetime.now() + timedelta(hours=self.missed_queue_ban)
            self.soft_bans_fcfs_queue.append({"id": user_id, "expires": ban_expiry})
            self.user_strikes[user_id] = 0 # Reset strikes after the ban is applied
            msg = f"🚫 **BANNED (3 Strikes Reached)**\n{reason}\nYou are banned from the queue for {self.missed_queue_ban} hours."
        else:
            msg = f"⚠️ **STRIKE {strikes}/3**\n{reason}\nIf you reach 3 strikes, you will be temporarily banned."
            
        try:
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            pass

    async def enforce_queue_turn(self, context):
        """Fires 5 minutes after a user reaches #1. Slides them down the queue or bans them."""
        job_data = context.job.data
        user_id = job_data["user_id"]
        machine_type = job_data["machine_type"]

        target_queue = self.washer_fcfs_queue if machine_type == "washer" else self.dryer_fcfs_queue
        
        # If the user is STILL in the #1 position, they never pressed START
        if len(target_queue) > 0 and target_queue[0]["id"] == user_id:
            user_data = target_queue.pop(0)
            new_strike_count = self.user_strikes.get(user_id, 0) + 1
            user_data.pop("turn_expiry", None)

            if new_strike_count >= 3:
                reason = f"You failed to start the {machine_type} within 5 minutes."
            else:
                # Progressive slide: Strike 1 = drop 2 spots, Strike 2 = drop 4 spots
                drop_spots = new_strike_count * 2
                insert_idx = min(len(target_queue), drop_spots) 
                target_queue.insert(insert_idx, user_data)
                
                if insert_idx > 0:
                    reason = f"You failed to start the {machine_type}. You have been moved back {insert_idx} spot(s)."
                else:
                    reason = f"You failed to start the {machine_type}. As the only one in line, you kept your spot but received a strike."
            
            # Issue the penalty and immediately trigger the next person in line
            await self.issue_strike(context, user_id, reason)
            await self.notify_next_in_queue(context, machine_type)

    async def join_fcfs_queue(self, query, context, user_id, intent, dry_time):
        if intent == "Dry Only":
            self.dryer_fcfs_queue.append({"id": user_id, "intent": intent, "dry_time": dry_time, "system": "Queue"})
            queue_len = len(self.dryer_fcfs_queue)
        else:
            self.washer_fcfs_queue.append({"id": user_id, "intent": intent, "dry_time": dry_time, "system": "Queue"})
            queue_len = len(self.washer_fcfs_queue)
        
        delay_msg = ""
        if intent == "Wash & Dry" and queue_len > 1:
            delay_msg = "\n\n⏳ *Paced Dispatch Active:* We will delay your washer ping so your clothes don't sit wet!"
        elif intent == "Wash Only" and queue_len > 1:
            delay_msg = "\n\n🚀 *System Leapfrog:* Because you don't need a dryer, you bypass the hold constraint."

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Go to Dashboard", callback_data="view_status")]])
        await query.edit_message_text(f"📍 **Added to Live Queue**\nPosition: #{queue_len}\nIntent: {intent}{delay_msg}", parse_mode="Markdown", reply_markup=kb)
        await self.notify_next_in_queue(context, "washer" if intent != "Dry Only" else "dryer")

    async def notify_next_in_queue(self, context, machine_type: str):
        now = datetime.now()
        free_washers = [m for m in self.available_machines if m in [0, 1]]
        free_dryers = [m for m in self.available_machines if m in [2, 3]]
        
        time_display = f"{self.turn_grace_period}s" if self.turn_grace_period < 60 else f"{int(self.turn_grace_period/60)}m"

        if machine_type == "washer" and len(self.washer_fcfs_queue) > 0:
            next_user = self.washer_fcfs_queue[0]["id"]
            upcoming_bookings = sum(1 for time_str, d in self.timeslot_bookings.items() 
                                    if d["user"] != next_user and not d.get("started") 
                                    and 0 < (datetime.strptime(time_str, "%Y-%m-%d %H:%M") - now).total_seconds() / 60.0 <= 60)
            
            if len(free_washers) > upcoming_bookings:
                # ⬇️ ADD THIS LINE to save the expiration time for the dashboard ⬇️
                self.washer_fcfs_queue[0]["turn_expiry"] = now + timedelta(seconds=self.turn_grace_period)
                
                msg = f"👀 **A washer is now available!**\nYou have {time_display} to press START, or you will receive a strike!"
                context.job_queue.run_once(self.enforce_queue_turn, self.turn_grace_period, data={"user_id": next_user, "machine_type": "washer"})
                try: await context.bot.send_message(chat_id=next_user, text=msg, parse_mode="Markdown")
                except Exception: pass

        elif machine_type == "dryer" and len(self.dryer_fcfs_queue) > 0:
            next_user = self.dryer_fcfs_queue[0]["id"]
            vip_dryer_waitlist = sum(1 for u in self.dryer_pending_start if u["id"] != next_user)
            
            if len(free_dryers) > vip_dryer_waitlist:
                # ⬇️ ADD THIS LINE to save the expiration time for the dashboard ⬇️
                self.dryer_fcfs_queue[0]["turn_expiry"] = now + timedelta(seconds=self.turn_grace_period)
                
                msg = f"🔥 **A dryer is now available!**\nYou have {time_display} to press START, or you will receive a strike!"
                context.job_queue.run_once(self.enforce_queue_turn, self.turn_grace_period, data={"user_id": next_user, "machine_type": "dryer"})
                try: await context.bot.send_message(chat_id=next_user, text=msg, parse_mode="Markdown")
                except Exception: pass

    # ==========================================
    # TELEGRAM HANDLERS
    # ==========================================
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text, kb = self.get_dashboard_ui(user_id)
        
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        elif update.callback_query:
            await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data

        # --- VIEW DASHBOARD ---
        if data in ["view_status", "main_menu"]:
            text, kb = self.get_dashboard_ui(user_id)
            if data == "view_status":
                text += f"\n\n_Last refreshed: {datetime.now().strftime('%H:%M:%S')}_"
            try:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            except BadRequest:
                await query.answer("Dashboard is already up to date!", show_alert=False)

        # --- INTERCEPT: BOOKING INTENT ---
        elif data == "flow_book":
            if self.is_user_banned(user_id):
                await query.answer("🚫 You are currently banned from booking timeslots.", show_alert=True)
                return
            
            is_running = user_id in self.washer_active_cycles or user_id in self.dryer_active_cycles
            is_pending = user_id in self.washer_pending_collection or user_id in self.dryer_pending_collection or next((u for u in self.dryer_pending_start if u["id"] == user_id), None)
            
            if self.has_active_booking(user_id) or is_running or is_pending:
                await query.answer("⚠️ Limit Reached: You must finish your current laundry cycle or cancel your existing reservation first.", show_alert=True)
                return
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧺 Wash & Dry", callback_data="intent_book_wd")],
                [InlineKeyboardButton("🌊 Wash Only (Hang dry)", callback_data="intent_book_wo")],
                [InlineKeyboardButton("♨️ Dry Only (Wet load)", callback_data="intent_book_do")],
                [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="view_status")]
            ])
            await query.edit_message_text("What do you need to do for this booking?", reply_markup=kb)

        # --- INTERCEPT: FCFS INTENT ---
        elif data == "flow_fcfs":
            # --- INTERCEPT BANNED USERS ---
            if self.is_user_banned(user_id):
                await query.answer("🚫 You are currently banned from joining the queue.", show_alert=True)
                return
                    
            in_w_queue = any(item["id"] == user_id for item in self.washer_fcfs_queue)
            in_d_queue = any(item["id"] == user_id for item in self.dryer_fcfs_queue)
            is_running = user_id in self.washer_active_cycles or user_id in self.dryer_active_cycles
            is_pending = user_id in self.washer_pending_collection or user_id in self.dryer_pending_collection or next((u for u in self.dryer_pending_start if u["id"] == user_id), None)
            
            if in_w_queue or in_d_queue or user_id in self.paused_users:
                await query.answer("⚠️ Limit Reached: You are already in the live queue or paused.", show_alert=True)
                return
            if self.has_active_booking(user_id) or is_running or is_pending:
                await query.answer("⚠️ Limit Reached: You are currently using a machine or have a reservation.", show_alert=True)
                return

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧺 Wash & Dry", callback_data="intent_fcfs_wd")],
                [InlineKeyboardButton("🌊 Wash Only (Hang dry)", callback_data="intent_fcfs_wo")],
                [InlineKeyboardButton("♨️ Dry Only (Wet load)", callback_data="intent_fcfs_do")],
                [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="view_status")]
            ])
            await query.edit_message_text("What do you need to queue for?", reply_markup=kb)

        # --- ROUTER: BOOKING PATH ---
        elif data.startswith("intent_book_"):
            intent_map = {"intent_book_wd": "Wash & Dry", "intent_book_wo": "Wash Only", "intent_book_do": "Dry Only"}
            context.user_data['current_intent'] = intent_map[data]
            
            buttons, now = [], datetime.now()
            for i in range(7):
                d_str = (now + timedelta(days=i)).strftime("%Y-%m-%d")
                display = "Today" if i == 0 else (now + timedelta(days=i)).strftime("%a, %b %d")
                buttons.append([InlineKeyboardButton(display, callback_data=f"date_{d_str}")])
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="flow_book")])
            await query.edit_message_text("📅 Select a date to book:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("date_"):
            selected_date = data.split("date_")[1]
            base = datetime.strptime(selected_date, "%Y-%m-%d")
            current_time = base.replace(hour=0, minute=0)
            end_time = base + timedelta(days=1)
            
            slot_buttons = []
            while current_time < end_time:
                s_str = current_time.strftime("%Y-%m-%d %H:%M")
                display = current_time.strftime("%H:%M")
                
                if s_str in self.timeslot_bookings:
                    booking = self.timeslot_bookings[s_str]
                    dry_time = booking.get("dry_time", 0)
                    step_minutes = 15 + dry_time 
                    current_time += timedelta(minutes=step_minutes)
                else:
                    if current_time >= datetime.now():
                        slot_buttons.append(InlineKeyboardButton(f"🟩 {display}", callback_data=f"book_{s_str}"))
                    current_time += timedelta(minutes=45)

            buttons = [slot_buttons[i:i + 3] for i in range(0, len(slot_buttons), 3)]
            buttons.append([InlineKeyboardButton("🔙 Dates", callback_data="flow_book")])
            await query.edit_message_text(f"📅 **24-Hour Schedule for {selected_date}**\nSelect an available 45-min block:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("book_"):
            slot_str = data.split("book_")[1]
            intent = context.user_data.get('current_intent', 'Wash & Dry')
            
            if intent == "Wash Only":
                await self.confirm_booking(query, context, user_id, slot_str, 0)
            else:
                context.user_data['pending_slot'] = slot_str
                context.user_data['action_type'] = 'book'
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("30 Minutes", callback_data="duration_30")],
                    [InlineKeyboardButton("45 Minutes", callback_data="duration_45")],
                    [InlineKeyboardButton("60 Minutes", callback_data="duration_60")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="flow_book")]
                ])
                await query.edit_message_text("Select your intended dryer duration:", reply_markup=kb)

        # --- ROUTER: FCFS PATH ---
        elif data.startswith("intent_fcfs_"):
            intent_map = {"intent_fcfs_wd": "Wash & Dry", "intent_fcfs_wo": "Wash Only", "intent_fcfs_do": "Dry Only"}
            intent = intent_map[data]
            context.user_data['current_intent'] = intent
            
            if intent == "Wash Only":
                await self.join_fcfs_queue(query, context, user_id, intent, 0)
            else:
                context.user_data['action_type'] = 'fcfs'
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("30 Minutes", callback_data="duration_30")],
                    [InlineKeyboardButton("45 Minutes", callback_data="duration_45")],
                    [InlineKeyboardButton("60 Minutes", callback_data="duration_60")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="flow_fcfs")]
                ])
                await query.edit_message_text("Select your intended dryer duration:", reply_markup=kb)

        # --- ROUTER: DURATION SELECTOR ---
        elif data.startswith("duration_"):
            duration = int(data.split("_")[1])
            action_type = context.user_data.get('action_type')
            intent = context.user_data.get('current_intent', 'Wash & Dry')
            
            if action_type == 'book':
                slot_str = context.user_data.get('pending_slot')
                await self.confirm_booking(query, context, user_id, slot_str, duration)
            elif action_type == 'fcfs':
                await self.join_fcfs_queue(query, context, user_id, intent, duration)

        # --- ACTIONS: CANCEL, PAUSE, LEAVE, COLLECT ---
        elif data.startswith("cancel_book_"):
            slot = data.split("cancel_book_")[1]
            if slot in self.timeslot_bookings:
                del self.timeslot_bookings[slot]
            text, kb = self.get_dashboard_ui(user_id)
            try:
                await query.edit_message_text(f"✅ **Booking Canceled.**\n\n{text}", parse_mode="Markdown", reply_markup=kb)
            except BadRequest:
                await query.answer("Booking already canceled!", show_alert=False)

        elif data == "fcfs_pause":
            if user_id in self.pause_cooldowns:
                time_since_resume = (datetime.now() - self.pause_cooldowns[user_id]).total_seconds()
                if time_since_resume < 900: 
                    rem_mins = int((900 - time_since_resume) // 60) or 1
                    await query.answer(f"⏳ Cooldown Active: You cannot pause again for {rem_mins} more minutes.", show_alert=True)
                    return
            
            # --- FIXED: Check both queues ---
            user_data = next((item for item in self.washer_fcfs_queue if item["id"] == user_id), None)
            queue_type = "washer"
            
            if not user_data:
                user_data = next((item for item in self.dryer_fcfs_queue if item["id"] == user_id), None)
                queue_type = "dryer"
                
            if user_data:
                if queue_type == "washer":
                    orig_idx = self.washer_fcfs_queue.index(user_data)
                    self.washer_fcfs_queue.remove(user_data)
                else:
                    orig_idx = self.dryer_fcfs_queue.index(user_data)
                    self.dryer_fcfs_queue.remove(user_data)
                    
                self.paused_users[user_id] = {
                    "expires": datetime.now() + timedelta(hours=2), 
                    "pause_time": datetime.now(),
                    "orig_idx": orig_idx,
                    "queue_type": queue_type, # Save which line they came from
                    "data": user_data
                }
                
            text, kb = self.get_dashboard_ui(user_id)
            try:
                await query.edit_message_text(f"⏸ **You are paused.** You will not receive strikes while paused.\n\n{text}", parse_mode="Markdown", reply_markup=kb)
            except BadRequest:
                await query.answer("You are already paused!", show_alert=False)

        elif data == "fcfs_resume":
            if user_id in self.paused_users:
                pause_data = self.paused_users.pop(user_id)
                user_data = pause_data["data"]
                queue_type = pause_data.get("queue_type", "washer") # Default to washer safety
                
                time_paused_seconds = (datetime.now() - pause_data["pause_time"]).total_seconds()
                
                # --- FIXED: Target the correct queue array ---
                target_queue = self.washer_fcfs_queue if queue_type == "washer" else self.dryer_fcfs_queue
                
                if time_paused_seconds <= 600: 
                    insert_idx = min(pause_data["orig_idx"], len(target_queue))
                    target_queue.insert(insert_idx, user_data)
                    status_msg = "▶️ **Resumed!** You successfully kept your original spot in line."
                else:
                    target_queue.append(user_data)
                    status_msg = "▶️ **Resumed at the back of the line.**\nBecause you were paused for more than 10 minutes, your original spot was forfeited."
                
                self.pause_cooldowns[user_id] = datetime.now()
                
            text, kb = self.get_dashboard_ui(user_id)
            try:
                await query.edit_message_text(f"{status_msg}\n\n{text}", parse_mode="Markdown", reply_markup=kb)
            except BadRequest:
                await query.answer("You have already resumed!", show_alert=False)
        
        elif data == "fcfs_leave":
            self.washer_fcfs_queue = [u for u in self.washer_fcfs_queue if u["id"] != user_id]
            self.dryer_fcfs_queue = [u for u in self.dryer_fcfs_queue if u["id"] != user_id]
            text, kb = self.get_dashboard_ui(user_id)
            try:
                await query.edit_message_text(f"✅ **You left the queue.**\n\n{text}", parse_mode="Markdown", reply_markup=kb)
            except BadRequest:
                await query.answer("You are already out of the queue!", show_alert=False)

        elif data == "collected_washer":
            if user_id in self.washer_pending_collection:
                await self.force_free_machine_and_penalize(context, user_id, "washer", penalized=False)
                text, kb = self.get_dashboard_ui(user_id)
                try:
                    await query.edit_message_text(f"✅ Washer cleared. Thank you!\n\n{text}", parse_mode="Markdown", reply_markup=kb)
                except BadRequest:
                    pass

        elif data == "collected_dryer":
            if user_id in self.dryer_pending_collection:
                await self.force_free_machine_and_penalize(context, user_id, "dryer", penalized=False)
                text, kb = self.get_dashboard_ui(user_id)
                try:
                    await query.edit_message_text(f"✅ Dryer cleared. Thank you!\n\n{text}", parse_mode="Markdown", reply_markup=kb)
                except BadRequest:
                    pass

        # --- ACTIONS: START HARDWARE ---
        elif data == "start_washer":
            # 1. Reject if banned
            if self.is_user_banned(user_id):
                await query.answer("🚫 You are currently banned from starting new wash cycles.", show_alert=True)
                return

            # 2. Prevent Double-Tap Hardware Blackhole
            if user_id in self.washer_active_cycles:
                await query.answer("You already have a washer running!", show_alert=True)
                return

            # 3. Verify user is authorized (#1 in queue or has an active booking)
            is_first_in_queue = len(self.washer_fcfs_queue) > 0 and self.washer_fcfs_queue[0]["id"] == user_id
            active_slot = next((slot for slot, d in self.timeslot_bookings.items() if d["user"] == user_id and not d.get("started")), None)
            
            if not is_first_in_queue and not active_slot:
                await query.answer("⚠️ You do not have an active turn or reservation.", show_alert=True)
                return

            now = datetime.now()
            upcoming_bookings = sum(
                1 for time_str, d in self.timeslot_bookings.items() 
                if d["user"] != user_id 
                and not d.get("started") 
                and 0 < (datetime.strptime(time_str, "%Y-%m-%d %H:%M") - now).total_seconds() / 60.0 <= 60
            )
            
            free_washers = [m for m in self.available_machines if m in [0, 1]]
            
            if len(free_washers) > upcoming_bookings:
                assigned_machine = free_washers[0]
                self.available_machines.remove(assigned_machine)
                
                if is_first_in_queue:
                    entry = self.washer_fcfs_queue.pop(0)
                    intent, system = entry.get("intent"), entry.get("system")
                else:
                    intent = self.timeslot_bookings[active_slot]["intent"]
                    system = "Time Slot"
                    self.timeslot_bookings[active_slot]["started"] = True 

                await self.trigger_switchbot_mqtt(assigned_machine, user_id)
                self.washer_active_cycles[user_id] = {
                    "intent": intent, "machine_id": assigned_machine, "system": system,
                    "end_time": now + timedelta(seconds=self.cycle_timer)
                }
                
                context.job_queue.run_once(self.cycle_completion, self.cycle_timer, data={"user_id": user_id, "machine_type": "washer", "system": system})
                text, kb = self.get_dashboard_ui(user_id)
                
                try:
                    await query.edit_message_text(f"✅ **Washer {'A' if assigned_machine == 0 else 'B'} Started!** Hardware locked.\n\n{text}", parse_mode="Markdown", reply_markup=kb)
                except BadRequest:
                    pass
            else:
                await query.answer("All free washers are locked for upcoming reservations.", show_alert=True)

        elif data == "start_dryer":
            # 1. Prevent Double-Tap Hardware Blackhole
            if user_id in self.dryer_active_cycles:
                await query.answer("You already have a dryer running!", show_alert=True)
                return

            transition_entry = next((u for u in self.dryer_pending_start if u["id"] == user_id), None)
            is_first_in_queue = len(self.dryer_fcfs_queue) > 0 and self.dryer_fcfs_queue[0]["id"] == user_id

            # 2. Check Ban: allow ONLY if finishing an active transition (Approach B)
            if self.is_user_banned(user_id) and not transition_entry:
                await query.answer("🚫 You are banned from starting dryers. Only active transfer cycles are permitted.", show_alert=True)
                return

            if not transition_entry and not is_first_in_queue:
                await query.answer("⚠️ You do not have an active dryer turn.", show_alert=True)
                return

            free_dryers = [m for m in self.available_machines if m in [2, 3]]
            vip_dryer_waitlist = sum(1 for u in self.dryer_pending_start if u["id"] != user_id)
            
            if not transition_entry and len(free_dryers) <= vip_dryer_waitlist:
                await query.answer("All free dryers are locked for VIP users transitioning from washers.", show_alert=True)
                return
            
            if len(free_dryers) > 0:
                assigned_machine = free_dryers[0]
                self.available_machines.remove(assigned_machine)
                
                if transition_entry:
                    self.dryer_pending_start.remove(transition_entry)
                    system_type = transition_entry.get("system", "Queue")
                    # UPDATE: Retrieve the intent from the transition rather than hardcoding
                    intent = transition_entry.get("intent", "Wash & Dry")
                    washer_penalized = transition_entry.get("washer_penalized", False)
                    booking = next((d for s, d in self.timeslot_bookings.items() if d["user"] == user_id), None)
                    dry_time_mins = booking.get("dry_time", 30) if booking else 30

                else:
                    entry = self.dryer_fcfs_queue.pop(0)
                    system_type = "Queue"
                    intent = entry.get("intent", "Dry Only")
                    dry_time_mins = entry.get("dry_time", 30)
                    washer_penalized = False

                # Live Implementation
                # dryer_timer_seconds = dry_time_mins * 60
                dryer_timer_seconds = self.cycle_timer # For demo
                
                await self.trigger_switchbot_mqtt(assigned_machine, user_id)
                self.dryer_active_cycles[user_id] = {
                    "intent": intent, "machine_id": assigned_machine, "system": system_type,
                    "end_time": datetime.now() + timedelta(seconds=dryer_timer_seconds),
                    "washer_penalized": washer_penalized
                }
                
                context.job_queue.run_once(self.cycle_completion, dryer_timer_seconds, data={"user_id": user_id, "machine_type": "dryer", "system": system_type})
                text, kb = self.get_dashboard_ui(user_id)
                
                try:
                    await query.edit_message_text(f"✅ **Dryer {'A' if assigned_machine == 2 else 'B'} Started!** Hardware locked.\n\n{text}", parse_mode="Markdown", reply_markup=kb)
                except BadRequest:
                    pass

        try:
            await query.answer()
        except Exception:
            pass

    # ==========================================
    # BACKGROUND JOBS & LOGIC
    # ==========================================
    async def confirm_booking(self, query, context, user_id, slot_str, dry_time):
        intent = context.user_data.get('current_intent', 'Wash & Dry')
        self.timeslot_bookings[slot_str] = {"user": user_id, "intent": intent, "dry_time": dry_time, "started": False}
        
        try:
            delay = (datetime.strptime(slot_str, "%Y-%m-%d %H:%M") - datetime.now()).total_seconds() - 900
            if delay > 0:
                context.job_queue.run_once(self.booking_reminder_job, delay, data={"user_id": user_id, "slot_str": slot_str})
        except Exception:
            pass
        
        msg = f"✅ **Confirmed!**\nSlot: `{slot_str}`\nIntent: {intent}"
        if dry_time > 0: msg += f"\nDryer Allocated: {dry_time} mins"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 View Dashboard", callback_data="view_status")]])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=kb)

    async def booking_reminder_job(self, context: ContextTypes.DEFAULT_TYPE):
        job_data = context.job.data
        try:
            await context.bot.send_message(chat_id=job_data["user_id"], text=f"⏰ **15-Minute Reminder!**\nYour reservation at `{job_data['slot_str']}` begins shortly.", parse_mode="Markdown")
        except Forbidden:
            pass

    async def cycle_completion(self, context: ContextTypes.DEFAULT_TYPE):
        job_data = context.job.data
        user_id = job_data["user_id"]
        machine_type = job_data.get("machine_type", "washer")
        system = job_data.get("system", "Queue")

        if machine_type == "washer" and user_id in self.washer_active_cycles:
            # Retrieve cycle data and inject the expiry timestamp
            cycle_data = self.washer_active_cycles.pop(user_id)
            cycle_data["expiry_time"] = datetime.now() + timedelta(seconds=self.grace_period)
            self.washer_pending_collection[user_id] = cycle_data
            
            context.job_queue.run_once(self.enforce_collection_timeout, self.grace_period, data={"user_id": user_id, "machine_type": "washer"})
            msg = "👕 **Washer Cycle Complete!**\nPlease collect your clothes within the next **15 minutes**. Press 'Claimed' on your dashboard when done."
            try: await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            except Forbidden: pass
            
        elif machine_type == "dryer" and user_id in self.dryer_active_cycles:
            # Retrieve cycle data and inject the expiry timestamp
            cycle_data = self.dryer_active_cycles.pop(user_id)
            cycle_data["expiry_time"] = datetime.now() + timedelta(seconds=self.grace_period)
            self.dryer_pending_collection[user_id] = cycle_data
            
            context.job_queue.run_once(self.enforce_collection_timeout, self.grace_period, data={"user_id": user_id, "machine_type": "dryer"})
            msg = "🔥 **Dryer Cycle Complete!**\nPlease collect your clothes within the next **15 minutes**. Press 'Claimed' on your dashboard when done."
            try: await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            except Forbidden: pass

    async def enforce_collection_timeout(self, context: ContextTypes.DEFAULT_TYPE):
        job_data = context.job.data
        user_id = job_data.get("user_id")
        machine_type = job_data.get("machine_type")
        if machine_type == "washer" and user_id in self.washer_pending_collection:
            await self.force_free_machine_and_penalize(context, user_id, "washer", penalized=True)
        elif machine_type == "dryer" and user_id in self.dryer_pending_collection:
            await self.force_free_machine_and_penalize(context, user_id, "dryer", penalized=True)

    async def force_free_machine_and_penalize(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, machine_type: str, penalized: bool = False):    
        msg = ""
        intent = ""             
        washer_penalized = False
        system = "Queue"
        
        if machine_type == "washer" and user_id in self.washer_pending_collection:
            pending = self.washer_pending_collection.pop(user_id)
            machine_id = pending.get("machine_id")
            intent = pending.get("intent", "Wash Only")
            system = pending.get("system", "Queue")
            
            if machine_id not in self.available_machines:
                self.available_machines.append(machine_id)
                self.available_machines.sort()
            
            if intent == "Wash & Dry":
                # Route both FCFS and Timeslot into dryer_pending_start
                self.dryer_pending_start.append({"id": user_id, "intent": "Wash & Dry", "system": system, "washer_penalized": penalized})
                msg = "Please proceed to an available Dryer and press START on your dashboard."
            
            await self.notify_next_in_queue(context, "washer")

        elif machine_type == "dryer" and user_id in self.dryer_pending_collection:
            pending = self.dryer_pending_collection.pop(user_id)
            machine_id = pending.get("machine_id")
            system = pending.get("system", "Queue")
            intent = pending.get("intent", "Dry Only") # <-- ADD THIS LINE to extract intent
            washer_penalized = pending.get("washer_penalized", False)

            if machine_id not in self.available_machines:
                self.available_machines.append(machine_id)
                self.available_machines.sort()
            await self.notify_next_in_queue(context, "dryer")
        
        # APPLY INSTANT BAN IF PENALIZED
        if penalized:
            # UPDATE: 72 hours (3 days) for Wash & Dry forgotten in washer & dryer, else 48 hours
            ban_hours = self.forgot_washer_and_dryer if (machine_type == "dryer" and intent == "Wash & Dry" and washer_penalized) else self.machine_forgot_ban
            ban_expiry = datetime.now() + timedelta(hours=ban_hours)
            
            if system == "Time Slot":
                self.soft_bans_bookings.append({"id": user_id, "expires": ban_expiry})
            else:
                self.soft_bans_fcfs_queue.append({"id": user_id, "expires": ban_expiry})
            
            pickup_window_str = f"{self.grace_period}s" if self.grace_period < 60 else f"{int(self.grace_period // 60)} minutes"
            ban_notice = f"🚨 **COLLECTION TIME EXPIRED ({ban_hours}h Ban Applied)**\nYou exceeded the {pickup_window_str} pickup window. You are banned from future queues and reservations for {ban_hours} hours.\n\n"
            msg = ban_notice + (msg if msg else "Your laundry slot has concluded.")
                    
        if msg:
            try:
                await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            except Forbidden:
                pass

if __name__ == "__main__":
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    bot = chopeWashBot(token)
    bot.runbot()