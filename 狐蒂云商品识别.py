import re
import json
import argparse
import os
import time
import asyncio
import sqlite3
from playwright.async_api import async_playwright
import requests

class HudiyunScanner:
    def send_dingtalk(self, message):
        webhook = os.environ.get("DINGTALK_WEBHOOK")
        if not webhook:
            return
        
        try:
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "狐蒂云监控通知",
                    "text": message
                }
            }
            requests.post(webhook, json=data, timeout=5)
        except Exception as e:
            print(f"Failed to send DingTalk notification: {e}")

    def __init__(self, start_pid=1850, end_pid=1900, get_price=True, time_limit=None, concurrency=5):
        self.start_pid = start_pid
        self.end_pid = end_pid
        self.get_price_flag = get_price
        self.time_limit = time_limit
        self.concurrency = concurrency
        
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.results_file = "hudiyun_results.json"
        self.results_path = os.path.join(self.script_dir, self.results_file)
        self.db_path = os.path.join(self.script_dir, "hudiyun.db")
        
        self.start_time = time.time()
        self.consecutive_failures = 0
        self.MAX_CONSECUTIVE_FAILURES = 50
        self.stop_signal = False
        
        # Initialize DB
        self.init_db()

    def init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        # Create table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                pid INTEGER PRIMARY KEY,
                title TEXT,
                price TEXT,
                billing_cycle TEXT,
                url TEXT,
                updated_at TEXT
            )
        ''')
        self.conn.commit()
        
        # Migrate JSON if DB is empty but JSON exists
        self.cursor.execute("SELECT count(*) FROM products")
        if self.cursor.fetchone()[0] == 0 and os.path.exists(self.results_path):
            print("Migrating existing JSON data to SQLite...")
            try:
                with open(self.results_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    success_list = data.get('success', [])
                    for item in success_list:
                        self.upsert_product(item)
                print(f"Migrated {len(success_list)} items.")
            except Exception as e:
                print(f"Migration failed: {e}")

    def upsert_product(self, item):
        self.cursor.execute('''
            INSERT OR REPLACE INTO products (pid, title, price, billing_cycle, url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            item['pid'], 
            item['title'], 
            item['price'], 
            item['billing_cycle'], 
            item['url'],
            time.strftime("%Y-%m-%d %H:%M:%S")
        ))
        self.conn.commit()

    def get_product(self, pid):
        self.cursor.execute("SELECT * FROM products WHERE pid = ?", (pid,))
        row = self.cursor.fetchone()
        if row:
            return dict(row)
        return None

    def export_json(self):
        print("Exporting SQLite to JSON for frontend...")
        self.cursor.execute("SELECT * FROM products ORDER BY pid ASC")
        rows = self.cursor.fetchall()
        success_ids = [dict(row) for row in rows]
        
        data = {
            'success': success_ids,
            'failed': [], # We don't strictly track failed IDs in DB to save space, keeping empty for compat
            'last_pid': success_ids[-1]['pid'] if success_ids else self.start_pid,
            'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        try:
            with open(self.results_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Export complete. {len(success_ids)} items.")
        except Exception as e:
            print(f"Error exporting JSON: {e}")

    async def check_pid(self, context, pid, semaphore):
        async with semaphore:
            if self.stop_signal:
                return

            if self.time_limit and (time.time() - self.start_time > self.time_limit):
                if not self.stop_signal:
                    print(f"\n⏰ Time limit reached ({self.time_limit}s). Stopping safely.")
                    self.stop_signal = True
                return

            url = f"https://www.szhdy.com/cart?action=configureproduct&pid={pid}"
            
            page = await context.new_page()
            try:
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                except Exception as e:
                    print(f"PID {pid}: ⚠️ Timeout/Error")
                    return

                try:
                    await page.wait_for_selector('.allocation-header-title h1, .maintain-text-title, .configureproduct', timeout=5000)
                except:
                    pass 

                title = await page.title()
                body_text = await page.inner_text('body')
                
                # Check 404/NotFound
                if '404' in title or '抱歉找不到页面' in body_text:
                    print(f"PID {pid}: ❌ Page Not Found")
                    self.consecutive_failures += 1
                    
                    # Optional: Check if it disappeared
                    # existing = self.get_product(pid)
                    # if existing:
                    #      print(f"PID {pid}: (Disappeared)")
                    #      # self.delete_product(pid) ?
                else:
                    product_name_el = await page.query_selector('.allocation-header-title h1')
                    product_name = await product_name_el.inner_text() if product_name_el else ""
                    product_name = product_name.strip()
                    
                    has_os_card = bool(await page.query_selector('.os-card'))
                    has_config_area = bool(await page.query_selector('.configureproduct'))
                    btn_buy_now = await page.query_selector('.btn-buyNow')
                    has_buy_button = btn_buy_now and bool((await btn_buy_now.inner_text()).strip())
                    
                    has_product_info = product_name or has_os_card or has_config_area

                    is_success = False
                    if has_product_info:
                            if (product_name or has_os_card) and has_buy_button:
                                is_success = True
                            elif has_config_area and has_buy_button:
                                is_success = True
                            elif len(await page.query_selector_all('.sky-cart-menu-item')) > 3 and not has_product_info:
                                is_success = False
                            else:
                                is_success = True

                    if not is_success:
                        print(f"PID {pid}: ❌ Invalid Product Page")
                        self.consecutive_failures += 1
                    else:
                        # Success
                        self.consecutive_failures = 0 

                        billing_cycle = "default"
                        try:
                            annual_radio = await page.query_selector('input[name="billingcycle"][value="annually"]')
                            if annual_radio:
                                await page.evaluate("document.querySelector('input[name=\"billingcycle\"][value=\"annually\"]').click()")
                                try:
                                    await page.wait_for_load_state('networkidle', timeout=3000)
                                except:
                                    pass
                                billing_cycle = "annually"
                        except:
                            pass

                        price = ""
                        try:
                            # Try dynamic element first
                            price_el = await page.wait_for_selector('.ordersummarybottom-price', timeout=3000)
                            if price_el:
                                raw_price = (await price_el.inner_text()).strip()
                                if not re.match(r'^\d+(\.\d+)?$', raw_price.replace(',', '')):
                                    positioning_el = await page.query_selector('.pricePositioning')
                                    if positioning_el:
                                        raw_price = (await positioning_el.inner_text()).strip() + raw_price
                                
                                cleaned_price = re.sub(r'[^\d.]', '', raw_price)
                                if cleaned_price:
                                    price = cleaned_price
                        except:
                            pass 
                        
                        if not price:
                            price_match = re.search(r'¥\s*([\d,]+\.?\d*)', body_text)
                            if price_match:
                                    price = price_match.group(1).replace(',', '')

                        current_price_fmt = f"¥{price}" if price else ""
                        
                        # Check high price
                        if price:
                            try:
                                price_val = float(price)
                                if price_val >= 9999:
                                    print(f"PID {pid}: ⚠️ High Price ({current_price_fmt}) - Keeping")
                            except:
                                pass
                        
                        cycle_str = " (年付)" if billing_cycle == "annually" else ""
                        print(f"PID {pid}: ✅ {product_name} {current_price_fmt}{cycle_str}")
                        
                        # CHANGE DETECTION & NOTIFICATION
                        existing_item = self.get_product(pid)
                        notification_msg = ""
                        
                        if not existing_item:
                            notification_msg = f"## ✨ 发现新商品 (PID: {pid})\n- **标题**: {product_name}\n- **价格**: {current_price_fmt}\n- **周期**: {billing_cycle}\n- [点击购买]({url})"
                        else:
                            old_price = existing_item.get('price', '')
                            if current_price_fmt and current_price_fmt != old_price:
                                notification_msg = f"## 💰 价格变动 (PID: {pid})\n- **标题**: {product_name}\n- **旧价格**: {old_price}\n- **新价格**: {current_price_fmt}\n- [点击购买]({url})"

                        if notification_msg:
                            self.send_dingtalk(notification_msg)

                        # UPSERT DB
                        new_item = {
                            'pid': pid,
                            'title': product_name,
                            'price': current_price_fmt,
                            'billing_cycle': billing_cycle,
                            'url': url
                        }
                        self.upsert_product(new_item)

            except Exception as e:
                print(f"PID {pid}: ⚠️ Error: {e}")
            finally:
                await page.close()

    async def run_async(self):
        print(f"Starting Async Scan (SQLite Mode) from PID {self.start_pid}...", end="")
        if self.end_pid:
             print(f" to {self.end_pid}")
        else:
             print(" (Continuous Mode)")
        
        if self.time_limit:
            print(f" [Time Limit: {self.time_limit}s]")
            
        print(f" [Concurrency: {self.concurrency}]")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                viewport={'width': 1280, 'height': 800}
            )
            
            semaphore = asyncio.Semaphore(self.concurrency)
            tasks = []
            
            pid = self.start_pid
            while not self.stop_signal:
                if self.end_pid and pid > self.end_pid:
                    break
                
                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    print(f"\n🛑 Stopping: {self.MAX_CONSECUTIVE_FAILURES} consecutive failures.")
                    break
                
                tasks = [t for t in tasks if not t.done()]
                
                task = asyncio.create_task(self.check_pid(context, pid, semaphore))
                tasks.append(task)
                
                if len(tasks) > self.concurrency * 2:
                    await asyncio.sleep(0.1)

                pid += 1
            
            if tasks:
                await asyncio.gather(*tasks)
                
            await browser.close()
        
        # Export at end
        self.export_json()
        self.conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hudiyun Product Scanner (Async Playwright + SQLite)")
    parser.add_argument("--start", type=int, default=1, help="Start PID (default: 1)")
    parser.add_argument("--end", type=int, default=None, help="End PID (optional, default: continuous)")
    parser.add_argument("--time-limit", type=int, default=None, help="Stop after N seconds")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent workers (default: 5)")
    
    args = parser.parse_args()
    
    scanner = HudiyunScanner(
        start_pid=args.start,
        end_pid=args.end,
        time_limit=args.time_limit,
        concurrency=args.concurrency
    )
    
    try:
        asyncio.run(scanner.run_async())
    except KeyboardInterrupt:
        print("\nManually interrupted. Exporting data...")
        scanner.export_json()
    except Exception as e:
        print(f"Fatal Error: {e}")
        scanner.export_json()
