from flask import Flask, render_template, jsonify, request, send_from_directory
import zipfile
import concurrent.futures
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from flask_cors import CORS
import time
import threading
import time
import logging
import atexit
from queue import Queue, Empty
from typing import Dict, Optional, List, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    WebDriverException,
    InvalidSessionIdException,
    TimeoutException,
    NoSuchWindowException
)


app = Flask(__name__)
CORS(app)  # This enables CORS for all routes

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('static/images', filename)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("WebDriverPool")

class WebDriverPool:
    """
    A pool of reusable WebDriver instances.
    
    This class manages a pool of WebDriver instances for Selenium automation,
    handling creation, validation, cleanup, and efficient reuse.
    """
    
    def __init__(self, 
                 max_drivers: int = 5,
                 max_driver_age: int = 1800,  # 30 minutes in seconds
                 chrome_driver_path: Optional[str] = None,
                 browser_options: Optional[Dict[str, Any]] = None):
        """
        Initialize the WebDriver pool.
        
        Args:
            max_drivers: Maximum number of driver instances in the pool
            max_driver_age: Maximum age of a driver in seconds before it's recycled
            chrome_driver_path: Path to chromedriver executable
            browser_options: Dictionary of browser options to use
        """
        self.driver_pool = Queue()
        self.active_drivers: Dict[int, dict] = {}
        self.max_drivers = max_drivers
        self.max_driver_age = max_driver_age
        self.chrome_driver_path = chrome_driver_path
        self.browser_options = browser_options or {}
        
        self.lock = threading.RLock()
        self.pool_size = 0
        
        # Start the maintenance thread
        self.running = True
        self.maintenance_thread = threading.Thread(
            target=self._maintenance_worker,
            daemon=True
        )
        self.maintenance_thread.start()
        
        # Register shutdown function
        atexit.register(self.shutdown)
        
        logger.info(f"WebDriverPool initialized with max_drivers={max_drivers}")
    
    def get_driver(self, timeout: int = 30) -> webdriver.Chrome:
        """
        Get a WebDriver instance from the pool or create a new one.
        
        Args:
            timeout: Maximum time in seconds to wait for an available driver
            
        Returns:
            A Chrome WebDriver instance
            
        Raises:
            TimeoutError: If no driver becomes available within the timeout period
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Try to get a driver from the pool
            try:
                driver_info = self.driver_pool.get(block=False)
                driver = driver_info["driver"]
                
                # Validate the driver
                if self._is_driver_valid(driver):
                    # Mark this driver as active
                    with self.lock:
                        self.active_drivers[id(driver)] = {
                            "driver": driver,
                            "created_at": driver_info["created_at"],
                            "last_used": time.time()
                        }
                    logger.debug(f"Retrieved existing driver from pool. Current pool size: {self.pool_size}")
                    return driver
                else:
                    # Driver is invalid, clean it up and try again
                    self._quit_driver(driver)
                    with self.lock:
                        self.pool_size -= 1
                    continue
            except Empty:
                # No drivers in the pool, create one if we haven't hit the limit
                with self.lock:
                    if self.pool_size < self.max_drivers:
                        driver = self._create_new_driver()
                        self.pool_size += 1
                        created_at = time.time()
                        self.active_drivers[id(driver)] = {
                            "driver": driver,
                            "created_at": created_at,
                            "last_used": created_at
                        }
                        logger.info(f"Created new driver. Current pool size: {self.pool_size}")
                        return driver
            
            # If we get here, we need to wait for a driver to become available
            time.sleep(0.5)
        
        # If we exit the loop, we've timed out
        raise TimeoutError("Timed out waiting for an available WebDriver")
    
    def release_driver(self, driver: webdriver.Chrome) -> None:
        """
        Return a driver to the pool.
        
        Args:
            driver: The WebDriver instance to return to the pool
        """
        if not driver:
            return
            
        driver_id = id(driver)
        
        with self.lock:
            if driver_id in self.active_drivers:
                driver_info = self.active_drivers.pop(driver_id)
                
                # Check if the driver is too old
                age = time.time() - driver_info["created_at"]
                if age > self.max_driver_age or not self._is_driver_valid(driver):
                    # Driver is too old or invalid, quit it
                    self._quit_driver(driver)
                    self.pool_size -= 1
                    logger.debug(f"Driver too old or invalid, removed from pool. Age: {age:.1f}s. Current pool size: {self.pool_size}")
                else:
                    # Return the driver to the pool
                    self.driver_pool.put({
                        "driver": driver,
                        "created_at": driver_info["created_at"]
                    })
                    logger.debug(f"Driver returned to pool. Current pool size: {self.pool_size}")
            else:
                # Unknown driver, just quit it
                self._quit_driver(driver)
                logger.warning("Unknown driver released, quitting it without affecting pool size")
    
    def _create_new_driver(self) -> webdriver.Chrome:
        """
        Create a new WebDriver instance with the configured options.
        
        Returns:
            A new Chrome WebDriver instance
        """
        chrome_options = Options()
        
        # Add default options for stability
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Add user-defined options
        for option, value in self.browser_options.items():
            if value is True:
                chrome_options.add_argument(f"--{option}")
            elif value is not None:
                chrome_options.add_argument(f"--{option}={value}")
                
        # Add headless option by default for server environments
        if "headless" not in self.browser_options:
            chrome_options.add_argument("--headless=new")
        
        # Create the driver
        if self.chrome_driver_path:
            service = Service(executable_path=self.chrome_driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            driver = webdriver.Chrome(options=chrome_options)
        
        # Set timeouts
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(10)
        driver.set_script_timeout(30)
        
        return driver
    
    def _is_driver_valid(self, driver: webdriver.Chrome) -> bool:
        """
        Check if a WebDriver instance is still valid.
        
        Args:
            driver: The WebDriver instance to check
            
        Returns:
            True if the driver is valid, False otherwise
        """
        if not driver:
            return False
            
        try:
            # Try a simple operation to check if the driver is responsive
            driver.current_url
            return True
        except (InvalidSessionIdException, NoSuchWindowException, WebDriverException):
            return False
    
    def _quit_driver(self, driver: webdriver.Chrome) -> None:
        """
        Safely quit a WebDriver instance.
        
        Args:
            driver: The WebDriver instance to quit
        """
        if not driver:
            return
            
        try:
            driver.quit()
        except Exception as e:
            logger.warning(f"Error quitting driver: {str(e)}")
    
    def _maintenance_worker(self) -> None:
        """
        Background maintenance worker that checks for stuck or old drivers.
        """
        while self.running:
            try:
                # Sleep first to allow initial setup
                time.sleep(30)
                
                with self.lock:
                    # Check for stuck active drivers (unused for a long time)
                    current_time = time.time()
                    stuck_drivers = []
                    
                    for driver_id, info in list(self.active_drivers.items()):
                        # If a driver has been active for more than 5 minutes, consider it stuck
                        if current_time - info["last_used"] > 300:  # 5 minutes
                            stuck_drivers.append((driver_id, info["driver"]))
                    
                    # Clean up stuck drivers
                    for driver_id, driver in stuck_drivers:
                        logger.warning(f"Found stuck driver (unused for >5min), cleaning up")
                        self._quit_driver(driver)
                        self.active_drivers.pop(driver_id, None)
                        self.pool_size -= 1
                
                # Check pool size consistency
                with self.lock:
                    expected_size = len(self.active_drivers) + self.driver_pool.qsize()
                    if expected_size != self.pool_size:
                        logger.warning(f"Pool size inconsistency detected: tracked={self.pool_size}, actual={expected_size}")
                        self.pool_size = expected_size
                    
            except Exception as e:
                logger.error(f"Error in maintenance worker: {str(e)}")
    
    def shutdown(self) -> None:
        """
        Shut down the pool and clean up all drivers.
        """
        logger.info("Shutting down WebDriverPool...")
        self.running = False
        
        # Clean up all drivers in the pool
        while not self.driver_pool.empty():
            try:
                driver_info = self.driver_pool.get(block=False)
                self._quit_driver(driver_info["driver"])
            except Empty:
                break
        
        # Clean up active drivers
        with self.lock:
            for info in list(self.active_drivers.values()):
                self._quit_driver(info["driver"])
            self.active_drivers.clear()
            self.pool_size = 0
        
        logger.info("WebDriverPool shutdown complete")


# Example usage with your Flask app:
def initialize_driver_pool() -> WebDriverPool:
    """Initialize the global WebDriver pool"""
    browser_options = {
        "disable-infobars": True,
        "disable-extensions": True,
        "disable-popup-blocking": True,
        "incognito": True,
        "headless": "new",  # Using new headless mode
    }
    
    return WebDriverPool(
        max_drivers=1,  # Adjust based on your server resources
        max_driver_age=1800,  # 30 minutes
        browser_options=browser_options
    )



def get_chrome_options():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--single-process")  # Try this if multi-process is causing issues
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.binary_location = "/opt/google/chrome/chrome"  # Path to the Chrome binary
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    return chrome_options

def configure_proxy_options_with_auth():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # For proxies that require authentication
    proxy = "your-proxy-ip:port"
    username = "your-username"
    password = "your-password"
    
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"]
        }
    }
    """
    
    background_js = """
    var config = {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "http",
                host: "%s",
                port: %s
            },
            bypassList: []
        }
    };
    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }
    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        {urls: ["<all_urls>"]},
        ['blocking']
    );
    """ % (proxy.split(':')[0], proxy.split(':')[1], username, password)
    
    # Create plugin
    plugin_file = 'proxy_auth_plugin.zip'
    with zipfile.ZipFile(plugin_file, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    
    chrome_options.add_extension(plugin_file)
    
    return chrome_options

def convert_to_gbp(price_string):
    # Handle empty or invalid prices
    if not price_string or price_string == "No price":
        return "Error fetching price"

    # Extract currency symbol and amount
    price_string = price_string.strip()

    # Handle different currency formats
    currency = "£"  # Default to GBP
    amount = 0

    # Japanese Yen (¥)
    if "¥" in price_string:
        try:
            # Extract numeric value
            amount = float(price_string.replace("¥", "").replace(",", "").strip())
            # Convert JPY to GBP (approximate exchange rate: 1 GBP = 190 JPY)
            amount = amount / 190.0
        except:
            return price_string

    # US Dollar ($)
    elif "$" in price_string:
        try:
            amount = float(price_string.replace("$", "").replace(",", "").strip())
            # Convert USD to GBP (approximate exchange rate: 1 GBP = 1.25 USD)
            amount = amount / 1.25
        except:
            return price_string

    # Euro (€)
    elif "€" in price_string:
        try:
            amount = float(price_string.replace("€", "").replace(",", "").strip())
            # Convert EUR to GBP (approximate exchange rate: 1 GBP = 1.15 EUR)
            amount = amount / 1.15
        except:
            return price_string

    # Already in GBP (£)
    elif "£" in price_string:
        try:
            amount = float(price_string.replace("£", "").replace(",", "").strip())
        except:
            return price_string

    # No currency symbol - try to extract just the number
    else:
        try:
            amount = float(price_string.replace(",", "").strip())
        except:
            return price_string

    # Format the amount to GBP with 2 decimal places
    return f"{currency}{amount:.2f}"


# Vinted Scraping Function
def scrape_vinted(query):
    driver = None
    # Set up Selenium options
    
    chrome_options = get_chrome_options()
    driver_pool = WebDriverPool()
    driver = driver_pool.get_driver(timeout=10)

    # chrome_options = configure_proxy_options()


    try:
        # Load the search page
        url = f"https://www.vinted.com/catalog?search_text={query}"
        driver.get(url)

        # Wait for product items to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".feed-grid__item"))
        )

        # Extract products
        items = []
        product_cards = driver.find_elements(By.CSS_SELECTOR, ".feed-grid__item")

        for product in product_cards:  # Limit to 10 results
            try:
                # Find the <a> tag with the title attribute
                link_element = product.find_element(By.CSS_SELECTOR, '[data-testid$="--overlay-link"]')
                title_full = link_element.get_attribute("title")  # Extract title attribute

                # Extracting the product URL
                link = link_element.get_attribute("href")

                # Extracting product image
                image_element = product.find_element(By.CSS_SELECTOR, '[data-testid$="--image--img"]')
                image = image_element.get_attribute("src") if image_element else "No image"

                # Extract title and price from `title` attribute
                title_parts = title_full.split(", ")
                title = title_parts[0] if len(title_parts) > 0 else "No title"
                price = title_parts[-2] if len(
                    title_parts) > 2 else "No price"  # Second last element usually contains price

                items.append({
                    "title": title,
                    "price": price,
                    "link": link,
                    "image": image,
                    "source": "vinted"
                })

            except Exception as e:
                print(f"❌ Error parsing product: {e}")

        return items

    finally:
        driver.quit()  # Close the browser


# Update the Depop scraping function
def scrape_depop(query):
    driver = None
    # Set up Selenium options
    chrome_options = get_chrome_options()
    driver_pool = WebDriverPool()
    driver = driver_pool.get_driver(timeout=10)

    # chrome_options = configure_proxy_options()


    try:
        url = f"https://www.depop.com/search/?q={query}"
        driver.get(url)

        # Wait for product grid to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "styles_productCardRoot__DaYPT"))
        )

        items = []
        product_cards = driver.find_elements(By.CLASS_NAME, "styles_productCardRoot__DaYPT")

        for product in product_cards:  # Limit to 10 results
            try:
                # Extract product URL correctly
                link_element = WebDriverWait(product, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a.styles_unstyledLink__DsttP"))
                )
                link = link_element.get_attribute("href")

                title1 = link.split("/")[-2].replace("-", " ").title()
                title = " ".join(title1.split()[1:]) if title1 and " " in title1 else ""

                image_element = WebDriverWait(product, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "img._mainImage_e5j9l_11"))
                )
                image = image_element.get_attribute("src") if image_element else "No image"

                # Improved price extraction with multiple fallback options
                try:
                    # First try the specific class
                    price_element = product.find_element(By.CSS_SELECTOR, "p.styles_price__H8qdh")
                    price = price_element.text
                except:
                    try:
                        # Try a more general selector based on aria-label
                        price_element = product.find_element(By.CSS_SELECTOR, "p[aria-label='Price']")
                        price = price_element.text
                    except:
                        try:
                            # Try yet another approach with class contains
                            price_element = product.find_element(By.CSS_SELECTOR, "p[class*='price']")
                            price = price_element.text
                        except:
                            price = "No price"

                # Convert price to GBP (if it's not already)
                converted_price = convert_to_gbp(price)

                items.append({
                    "title": title,
                    "price": converted_price,
                    "original_price": price,  # Keep original price for reference
                    "link": link,
                    "image": image,
                    "source": "depop"
                })

            except Exception as e:
                print(f"❌ Error parsing product: {e}")

        return items

    finally:
        driver.quit()


# def scrape_depop(query):
#     # Set up Selenium options
#     chrome_options = Options()
#     chrome_options.add_argument("--disable-gpu")
#     chrome_options.add_argument("--no-sandbox")
#     chrome_options.add_argument("--disable-dev-shm-usage")
#     chrome_options.add_argument("--headless")  # Headless mode re-enabled
#     chrome_options.add_argument(
#         "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
#
#     # Initialize WebDriver
#     driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
#
#     try:
#         url = f"https://www.depop.com/search/?q={query}"
#         driver.get(url)
#
#         # Wait for product grid to load
#         WebDriverWait(driver, 15).until(
#             EC.presence_of_element_located((By.CLASS_NAME, "styles_productCardRoot__DaYPT"))
#         )
#
#         items = []
#         product_cards = driver.find_elements(By.CLASS_NAME, "styles_productCardRoot__DaYPT")
#
#         for product in product_cards[:10]:  # Limit to 10 results
#             try:
#                 # Extract product URL correctly
#                 link_element = WebDriverWait(product, 5).until(
#                     EC.presence_of_element_located((By.CSS_SELECTOR, "a.styles_unstyledLink__DsttP"))
#                 )
#                 link = link_element.get_attribute("href")  # FIXED: Removed redundant base URL
#
#                 title1 = link.split("/")[-2].replace("-", " ").title()
#                 title = " ".join(title1.split()[1:]) if title1 and " " in title1 else ""
#
#                 image_element = WebDriverWait(product, 5).until(
#                     EC.presence_of_element_located((By.CSS_SELECTOR, "img._mainImage_e5j9l_11"))
#                 )
#                 image = image_element.get_attribute("src") if image_element else "No image"
#
#                 price_element = WebDriverWait(product, 5).until(
#                     EC.presence_of_element_located((By.CSS_SELECTOR, "p.styles_price__H8qdh"))
#                 )
#                 price = price_element.text if price_element else "No price"
#
#                 items.append({"title": title, "price": price, "link": link, "image": image, "source": "depop"})
#
#             except Exception as e:
#                 print(f"❌ Error parsing product: {e}")
#
#         return items
#
#     finally:
#         driver.quit()


def scrape_mercari(query):
    driver = None
    chrome_options = get_chrome_options()
    driver_pool = WebDriverPool()
    driver = driver_pool.get_driver(timeout=10)



    try:
        url = f"https://www.mercari.com/jp/search/?keyword={query}"
        driver.get(url)

        # Wait for items to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="item-cell"]'))
        )

        items = []
        product_cards = driver.find_elements(By.CSS_SELECTOR, '[data-testid="item-cell"]')

        for product in product_cards:  # Limit to 10 results
            try:
                # Extract product link safely
                link_element = product.find_elements(By.CSS_SELECTOR, 'a[data-testid="thumbnail-link"]')
                link = link_element[0].get_attribute("href")

                # Extract image & title safely
                image_element = product.find_elements(By.CSS_SELECTOR, "picture img")
                if not image_element:
                    print("⚠️ Skipping item: No image found")
                    continue  # Skip item if no image
                image = image_element[0].get_attribute("src")
                title = image_element[0].get_attribute("alt")

                # Extract price safely
                price_element = product.find_elements(By.CSS_SELECTOR, "span[class^='number']")
                price = price_element[0].text if price_element else "Error fetching price"

                price = convert_to_gbp(price)

                # And then when adding items to the list, include both original and converted prices
                items.append({
                    "title": title,
                    "price": price,  # This will be the converted GBP price
                    "link": link,
                    "image": image,
                    "source": "mercari"
                })

            except Exception as e:
                print(f"❌ Error parsing product: {e}")

        return items

    finally:
        driver.quit()


def scrape_ebay(query):
    driver = None
    chrome_options = get_chrome_options()
    driver_pool = WebDriverPool()
    driver = driver_pool.get_driver(timeout=10)
    url = f"https://www.ebay.co.uk/sch/i.html?_nkw={query.replace(' ', '+')}&_ipg=240"
    driver.get(url)

    # Wait until the listings are loaded
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.s-item'))
        )
    except Exception as e:
        print("Error: Listings not loaded in time.")
        driver.quit()
        return []

    products = []

    # Get all the products on the page
    product_elements = driver.find_elements(By.CSS_SELECTOR, '.s-item')

    for product in product_elements[:12]:
        try:
            # Wait explicitly for each important element in the product
            title_element = WebDriverWait(product, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.s-item__title'))
            )
            price_element = WebDriverWait(product, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.s-item__price'))
            )
            image_element = WebDriverWait(product, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.s-item__image img'))  # Updated selector
            )
            url_element = WebDriverWait(product, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.s-item__link'))
            )

            # Extract the details
            title = title_element.text
            price = price_element.text
            image_url = image_element.get_attribute('src')
            product_url = url_element.get_attribute('href')

            products.append({
                'title': title,
                'price': price,
                'link': product_url,
                'image': image_url,
                'source': "ebay"
            })

        except Exception as e:
            print(f"Error extracting details from a product: {e}")
            continue  # Skip this product if there's an error

    # Close the driver
    driver.quit()

    return products[-10:]


# New route for progressive search results
@app.route('/progressive-search', methods=['GET'])
def progressive_search():
    query = request.args.get('query', type=str)
    source = request.args.get('source', type=str)

    if not query:
        return jsonify({"error": "Query parameter 'query' is required"}), 400

    # Map source names to scraping functions
    scrapers = {
        'vinted': scrape_vinted,
        'depop': scrape_depop,
        'mercari': scrape_mercari,
        'ebay': scrape_ebay
    }

    # Check if requested source is valid
    if source not in scrapers:
        return jsonify({"error": f"Invalid source: {source}"}), 400

    # Execute the appropriate scraping function
    results = scrapers[source](query)
    return jsonify(results)


# Maintain the original combined search for backwards compatibility
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', type=str)

    if not query:
        return jsonify({"error": "Query parameter 'query' is required"}), 400

    # Run all scrapers concurrently
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Submit all scraping tasks
        vinted_future = executor.submit(scrape_vinted, query)
        depop_future = executor.submit(scrape_depop, query)
        mercari_future = executor.submit(scrape_mercari, query)
        ebay_future = executor.submit(scrape_ebay, query)

        # Get results as they complete
        vinted_results = vinted_future.result()
        depop_results = depop_future.result()
        mercari_results = mercari_future.result()
        ebay_results = ebay_future.result()

    # Combine results and return them
    all_results = vinted_results + depop_results + mercari_results + ebay_results
    return jsonify(all_results)


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)
