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

app = Flask(__name__)
CORS(app)  # This enables CORS for all routes

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('static/images', filename)

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
    # Set up Selenium options
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")  # Important for running as non-root
    chrome_options.add_argument("--disable-dev-shm-usage")  # Helps with memory issues
    chrome_options.add_argument("--remote-debugging-port=9222")  # For debugging
    chrome_options.add_argument("--window-size=1920,1080")  # Set a standard window size
    chrome_options = configure_proxy_options()

    # Initialize WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

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
    # Set up Selenium options
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    chrome_options.add_argument("--remote-debugging-port=9222")  # For debugging
    chrome_options.add_argument("--window-size=1920,1080")  # Set a standard window size
    chrome_options = configure_proxy_options()

    # Initialize WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

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
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    chrome_options.add_argument("--remote-debugging-port=9222")  # For debugging
    chrome_options.add_argument("--window-size=1920,1080")  # Set a standard window size
    chrome_options = configure_proxy_options()

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

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
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")  # Important for running as non-root
    chrome_options.add_argument("--disable-dev-shm-usage")  # Helps with memory issues
    chrome_options.add_argument("--remote-debugging-port=9222")  # For debugging
    chrome_options.add_argument("--window-size=1920,1080")  # Set a standard window size
    chrome_options = configure_proxy_options()

    # Automatically handle chromedriver installation and path
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

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
