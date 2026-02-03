import re
import json
import argparse
import os
import time
import asyncio
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
        self.success_ids = []
        self.failed_ids = []
        self.results_file = "hudiyun_results.json"
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.results_path = os.path.join(self.script_dir, self.results_file)
        self.start_time = time.time()
        
        self.load_results()
        self.existing_map = {item['pid']: item for item in self.success_ids}
        self.consecutive_failures = 0
        self.MAX_CONSECUTIVE_FAILURES = 50
        self.stop_signal = False

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
        # Sort by PID before saving
        self.success_ids.sort(key=lambda x: x['pid'])
        data = {
            'success': self.success_ids,
            'failed': self.failed_ids,
            'last_pid': self.success_ids[-1]['pid'] if self.success_ids else self.start_pid,
            'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(self.results_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving results: {e}")

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
            # print(f"Checking PID {pid}...", end='', flush=True) # Async printing is messy, skipping detailed start log
            
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
                
                if '404' in title or '抱歉找不到页面' in body_text:
                    print(f"PID {pid}: ❌ Page Not Found")
                    self.consecutive_failures += 1
                    if pid in self.existing_map:
                         print(f"PID {pid}: (Disappeared)")
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
                        # Success - Reset consecutive failures
                        # Note: In async, this counter logic is loose but sufficient for rough stopping
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
                        
                        # Notification Logic
                        notification_msg = ""
                        if pid not in self.existing_map:
                            notification_msg = f"## ✨ 发现新商品 (PID: {pid})\n- **标题**: {product_name}\n- **价格**: {current_price_fmt}\n- **周期**: {billing_cycle}\n- [点击购买]({url})"
                        else:
                            old_item = self.existing_map[pid]
                            old_price = old_item.get('price', '')
                            if current_price_fmt and current_price_fmt != old_price:
                                notification_msg = f"## 💰 价格变动 (PID: {pid})\n- **标题**: {product_name}\n- **旧价格**: {old_price}\n- **新价格**: {current_price_fmt}\n- [点击购买]({url})"

                        if notification_msg:
                            self.send_dingtalk(notification_msg)

                        # Update in-memory list safely (append only, sort later or lock if needed)
                        # Remove old
                        self.success_ids = [item for item in self.success_ids if item['pid'] != pid]
                        
                        new_item = {
                            'pid': pid,
                            'title': product_name,
                            'price': current_price_fmt,
                            'billing_cycle': billing_cycle,
                            'url': url
                        }
                        self.success_ids.append(new_item)
                        self.existing_map[pid] = new_item
                        
                        # Save periodically or here? 
                        # Saving here might be too frequent for async concurrency.
                        # Let's save every success for safety? Or maybe batch?
                        # Since user wants feedback, saving is safer.
                        self.save_results()

            except Exception as e:
                print(f"PID {pid}: ⚠️ Error: {e}")
            finally:
                await page.close()

    async def run_async(self):
        print(f"Starting Async Scan from PID {self.start_pid}...", end="")
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
                
                # Check consecutive failures in main loop to break
                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    print(f"\n🛑 Stopping: {self.MAX_CONSECUTIVE_FAILURES} consecutive failures.")
                    break
                
                # Clean up completed tasks
                tasks = [t for t in tasks if not t.done()]
                
                # Add new task
                task = asyncio.create_task(self.check_pid(context, pid, semaphore))
                tasks.append(task)
                
                # Flow control: Don't spawn infinitely if semaphore is full? 
                # Semaphore handles execution, but tasks list grows.
                # Just limit pending tasks to avoid OOM if very fast?
                if len(tasks) > self.concurrency * 2:
                    await asyncio.sleep(0.1)

                pid += 1
            
            # Wait for remaining tasks
            if tasks:
                await asyncio.gather(*tasks)
                
            await browser.close()
            
        print(f"\nScan complete. Found {len(self.success_ids)} valid products.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hudiyun Product Scanner (Async Playwright)")
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
    
    asyncio.run(scanner.run_async())
