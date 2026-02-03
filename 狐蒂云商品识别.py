import re
import json
import argparse
import os
import time
from playwright.sync_api import sync_playwright
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

    def __init__(self, start_pid=1850, end_pid=1900, get_price=True, time_limit=None):
        self.start_pid = start_pid
        self.end_pid = end_pid
        self.get_price_flag = get_price
        self.time_limit = time_limit
        self.success_ids = []
        self.failed_ids = []
        self.results_file = "hudiyun_results.json"
        
        # Determine script directory to save results in the same folder
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.results_path = os.path.join(self.script_dir, self.results_file)

        self.load_results()

    def load_results(self):
        if os.path.exists(self.results_path):
            try:
                with open(self.results_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.success_ids = data.get('success', [])
                    self.failed_ids = data.get('failed', [])
            except Exception as e:
                print(f"Error loading results: {e}")

    def save_results(self):
        data = {
            'success': self.success_ids,
            'failed': self.failed_ids
        }
        try:
            with open(self.results_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving results: {e}")

    def scan(self):
        print(f"Starting scan from PID {self.start_pid}...", end="")
        if self.end_pid:
             print(f" to {self.end_pid}")
        else:
             print(" (Continuous Mode)")
        
        if self.time_limit:
            print(f" [Time Limit: {self.time_limit}s]")
        
        # Create lookup map for existing data
        existing_map = {item['pid']: item for item in self.success_ids}

        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                viewport={'width': 1280, 'height': 800}
            )
            
            page = context.new_page()
            
            pid = self.start_pid
            consecutive_failures = 0
            MAX_CONSECUTIVE_FAILURES = 50 

            while True:
                # Check Time Limit
                if self.time_limit and (time.time() - self.start_time > self.time_limit):
                    print(f"\n⏰ Time limit reached ({self.time_limit}s). Stopping safely.")
                    break

                if self.end_pid and pid > self.end_pid:
                    break
                
                # Removed "Skip" logic to allow re-scanning for updates.
                
                url = f"https://www.szhdy.com/cart?action=configureproduct&pid={pid}"
                print(f"Checking PID {pid}...", end='', flush=True)

                try:
                    # Navigate and wait for content
                    try:
                        page.goto(url, wait_until='domcontentloaded', timeout=10000)
                    except Exception as e:
                        print(f" ⚠️ Timeout/Error: {e}")
                        pid += 1
                        continue

                    # Wait for key elements
                    try:
                        page.wait_for_selector('.allocation-header-title h1, .maintain-text-title, .configureproduct', timeout=3000)
                    except:
                        pass 

                    # Check HTTP status equivalent
                    title = page.title()
                    body_text = page.inner_text('body')
                    
                    if '404' in title or '抱歉找不到页面' in body_text:
                        print(f" ❌ Page Not Found (404)")
                        consecutive_failures += 1
                        
                        if pid in existing_map:
                             print(" (Disappeared)")
                    else:
                        # Check Success
                        product_name_el = page.query_selector('.allocation-header-title h1')
                        product_name = product_name_el.inner_text().strip() if product_name_el else ""
                        
                        has_os_card = bool(page.query_selector('.os-card'))
                        has_config_area = bool(page.query_selector('.configureproduct'))
                        btn_buy_now = page.query_selector('.btn-buyNow')
                        has_buy_button = btn_buy_now and bool(btn_buy_now.inner_text().strip())
                        
                        has_product_info = product_name or has_os_card or has_config_area

                        is_success = False
                        if has_product_info:
                             if (product_name or has_os_card) and has_buy_button:
                                 is_success = True
                             elif has_config_area and has_buy_button:
                                 is_success = True
                             elif len(page.query_selector_all('.sky-cart-menu-item')) > 3 and not has_product_info:
                                 is_success = False
                             else:
                                 is_success = True

                        if not is_success:
                            print(" ❌ Not a valid product page")
                            consecutive_failures += 1
                        else:
                            # SUCCESS
                            consecutive_failures = 0 # Reset counter

                            # Try to switch to Annual Billing
                            billing_cycle = "default"
                            try:
                                annual_radio = page.query_selector('input[name="billingcycle"][value="annually"]')
                                if annual_radio:
                                    page.evaluate("document.querySelector('input[name=\"billingcycle\"][value=\"annually\"]').click()")
                                    try:
                                        page.wait_for_load_state('networkidle', timeout=3000)
                                    except:
                                        pass
                                    billing_cycle = "annually"
                                    print(" (年付)", end='')
                            except:
                                pass

                            # Get Price
                            price = ""
                            try:
                                price_el = page.wait_for_selector('.ordersummarybottom-price', timeout=2000)
                                if price_el:
                                    raw_price = price_el.inner_text().strip()
                                    if not re.match(r'^\d+(\.\d+)?$', raw_price.replace(',', '')):
                                        positioning_el = page.query_selector('.pricePositioning')
                                        if positioning_el:
                                            raw_price = positioning_el.inner_text().strip() + raw_price
                                    
                                    cleaned_price = re.sub(r'[^\d.]', '', raw_price)
                                    if cleaned_price:
                                        price = cleaned_price
                            except:
                                pass 
                            
                            if not price:
                                price_match = re.search(r'¥\s*([\d,]+\.?\d*)', body_text)
                                if price_match:
                                     price = price_match.group(1).replace(',', '')

                            # Log Price logic
                            current_price_fmt = f"¥{price}" if price else ""
                            
                            if price:
                                try:
                                    price_val = float(price)
                                    if price_val >= 9999:
                                        print(f" ⚠️ High Price Detected ({current_price_fmt}) - Keeping")
                                except:
                                    pass
                            
                            print(f" ✅ Success! {product_name} {current_price_fmt}")
                            
                            # CHANGE DETECTION & NOTIFICATION
                            notification_msg = ""
                            if pid not in existing_map:
                                # New Item Found (only notify if valid price)
                                notification_msg = f"## ✨ 发现新商品 (PID: {pid})\n- **标题**: {product_name}\n- **价格**: {current_price_fmt}\n- **周期**: {billing_cycle}\n- [点击购买]({url})"
                                print("   -> New Item Discovered!")
                            else:
                                old_item = existing_map[pid]
                                old_price = old_item.get('price', '')
                                if current_price_fmt and current_price_fmt != old_price:
                                    notification_msg = f"## 💰 价格变动 (PID: {pid})\n- **标题**: {product_name}\n- **旧价格**: {old_price}\n- **新价格**: {current_price_fmt}\n- [点击购买]({url})"
                                    print(f"   -> Price changed: {old_price} -> {current_price_fmt}")
                                elif old_item.get('billing_cycle', 'default') != billing_cycle:
                                     pass

                            if notification_msg:
                                self.send_dingtalk(notification_msg)

                            # Update Data
                            # Remove old entry if exists to replace with new
                            self.success_ids = [item for item in self.success_ids if item['pid'] != pid]
                            
                            new_item = {
                                'pid': pid,
                                'title': product_name,
                                'price': current_price_fmt,
                                'billing_cycle': billing_cycle,
                                'url': url
                            }
                            self.success_ids.append(new_item)
                            existing_map[pid] = new_item 
                            
                            # Save immediately on success
                            self.success_ids.sort(key=lambda x: x['pid'])
                            self.save_results()

                except Exception as e:
                    print(f" ⚠️ Error: {e}")
                
                # Check consecutive failures stop condition
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n🛑 Stopping: Refused connection or invalid pages for {MAX_CONSECUTIVE_FAILURES} consecutive PIDs.")
                    break
                
                pid += 1

            browser.close()
        
        print(f"\nScan complete. Found {len(self.success_ids)} valid products.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hudiyun Product Scanner (Playwright)")
    parser.add_argument("--start", type=int, default=1, help="Start PID (default: 1)")
    parser.add_argument("--end", type=int, default=None, help="End PID (optional, default: continuous)")
    parser.add_argument("--time-limit", type=int, default=None, help="Stop after N seconds (e.g. 2400 for 40 mins)")
    
    args = parser.parse_args()
    
    scanner = HudiyunScanner(
        start_pid=args.start,
        end_pid=args.end,
        time_limit=args.time_limit
    )
    scanner.scan()
