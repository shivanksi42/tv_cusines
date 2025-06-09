from flask import Flask, jsonify, request
import requests
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional, Set, Tuple
import logging
from functools import wraps
import time
from dotenv import load_dotenv
import os
from flask_cors import CORS
from urllib.parse import urljoin, urlparse

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

class Config:
    BASE_URL = os.getenv("BACKEND_BASE_URL", "https://api.staging.tracevenue.com")
    ENDPOINT = os.getenv("FILTERED_VARIANTS_ENDPOINT", "/api/v1/traceVenue/variant/filteredVariants")
    TIMEOUT = int(os.getenv("TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    
    def __post_init__(self):
        # Validate and clean URLs
        self.BASE_URL = self.BASE_URL.rstrip('/')
        if not self.ENDPOINT.startswith('/'):
            self.ENDPOINT = '/' + self.ENDPOINT
        
        # Validate base URL
        parsed = urlparse(self.BASE_URL)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid BASE_URL: {self.BASE_URL}")
        
        logger.info(f"Base URL: {self.BASE_URL}")
        logger.info(f"Endpoint: {self.ENDPOINT}")
        logger.info(f"Full URL: {self.BASE_URL}{self.ENDPOINT}")

config = Config()
config.__post_init__()

def handle_api_errors(f):
    """Decorator to handle API errors gracefully"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {str(e)}")
            return jsonify({
                'error': 'Failed to fetch data from external API',
                'message': str(e)
            }), 503
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return jsonify({
                'error': 'Internal server error',
                'message': str(e)
            }), 500
    return decorated_function

def fetch_variants_data(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch variants data from the external API
    
    Args:
        request_payload: Request payload to send to the API (required)
        
    Returns:
        API response data
    """
    # Use urljoin for safer URL construction
    url = urljoin(config.BASE_URL + '/', config.ENDPOINT.lstrip('/'))
    
    # Alternative: Manual construction with validation
    # url = f"{config.BASE_URL}{config.ENDPOINT}"
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    
    # Log the actual URL being used
    logger.info(f"Constructed URL: {url}")
    logger.info(f"Base URL: {config.BASE_URL}")
    logger.info(f"Endpoint: {config.ENDPOINT}")
    
    for attempt in range(config.MAX_RETRIES):
        try:
            logger.info(f"Fetching data from {url} (attempt {attempt + 1})")
            logger.info(f"Payload: {request_payload}")
            logger.info(f"Headers: {headers}")
            
            response = requests.post(
                url, 
                json=request_payload, 
                headers=headers, 
                timeout=config.TIMEOUT
            )
            
            logger.info(f"Response Status: {response.status_code}")
            logger.info(f"Response Headers: {dict(response.headers)}")
            
            response_text = response.text
            logger.info(f"Response Content (first 500 chars): {response_text[:500]}...")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            if attempt == config.MAX_RETRIES - 1:
                raise Exception(f"API returned {response.status_code}: {response.text}")
            logger.warning(f"Attempt {attempt + 1} failed with HTTP error. Retrying...")
            time.sleep(2 ** attempt)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {str(e)}")
            if attempt == config.MAX_RETRIES - 1:
                raise
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying...")
            time.sleep(2 ** attempt)

def extract_categories_from_menu_item(menu_item: Dict) -> Set[Tuple[str, str]]:
    """
    Extract categories from a menu item, prioritizing parent categories.
    Returns set of (category_id, category_name) tuples.
    """
    categories = set()
    
    for category in menu_item.get('category', []):
        parent_categories = category.get('parentCategories', [])
        
        if parent_categories:
            # Use parent category if available
            for parent_cat in parent_categories:
                categories.add((parent_cat.get('_id', ''), parent_cat.get('name', '')))
        else:
            # Use original category if no parent category
            categories.add((category.get('_id', ''), category.get('name', '')))
    
    return categories

def extract_services_from_variant(variant: Dict) -> Tuple[List[str], List[str]]:
    """
    Extract free and paid services from a variant's freeServices and paidServices arrays.
    
    Args:
        variant: Variant dictionary containing freeServices and paidServices arrays
        
    Returns:
        Tuple of (free_services_names, paid_services_names)
    """
    free_services = []
    paid_services = []
    
    # Extract free services
    free_services_array = variant.get('freeServices', [])
    for service in free_services_array:
        service_name = service.get('serviceName', '')
        if service_name:
            free_services.append(service_name)
    
    # Extract paid services
    paid_services_array = variant.get('paidServices', [])
    for service in paid_services_array:
        service_name = service.get('serviceName', '')
        if service_name:
            paid_services.append(service_name)
    
    return free_services, paid_services

def parse_restaurant_variants(api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse restaurant variants data to extract cuisine combinations and detailed statistics.
    """
    variants = api_response.get('variants', [])
    if not variants:
        return {
            'restaurant_data': [],
            'sorted_cuisine_combinations': [],
            'all_cuisine_ids': [],
            'summary': {
                'total_variants': 0,
                'total_unique_cuisine_combinations': 0,
                'total_unique_cuisines': 0,
                'restaurant_id': None
            }
        }
    
    restaurant_id = variants[0].get('packageId', 'unknown')
    
    restaurant_variants = []
    cuisine_combinations = []
    all_cuisine_ids = set()
    
    # Process each variant
    for variant in variants:
        variant_id = variant.get('_id', 'unknown')
        variant_name = variant.get('name', 'unnamed')
        menu_items = variant.get('menuItems', [])
        venue_id = variant.get('venueId', '')
        cost = variant.get('cost', 0)
        
        # Extract services using the new function
        free_services_names, paid_services_names = extract_services_from_variant(variant)
        
        variant_cuisines = set()
        variant_categories = set()
        
        for menu_item in menu_items:
            # Extract cuisines
            cuisines = menu_item.get('cuisine', [])
            for cuisine_id in cuisines:
                variant_cuisines.add(cuisine_id)
                all_cuisine_ids.add(cuisine_id)
            
            # Extract categories
            categories = extract_categories_from_menu_item(menu_item)
            variant_categories.update(categories)
        
        variant_cuisine_list = sorted(list(variant_cuisines))
        
        restaurant_variants.append({
            'variant_id': variant_id,
            'variant_name': variant_name,
            'cuisines': variant_cuisine_list,
            'cuisine_count': len(variant_cuisine_list),
            'menu_items_count': len(menu_items),
            'categories': list(variant_categories),
            'categories_count': len(variant_categories),
            'cost': cost,
            'min_persons': variant.get('minPersons', 0),
            'max_persons': variant.get('maxPersons', 0),
            'is_customized': variant.get('isCustomized', False),
            'venue_id': venue_id,
            'free_services': free_services_names,
            'paid_services': paid_services_names,
            'free_services_count': len(free_services_names),
            'paid_services_count': len(paid_services_names)
        })
        
        if variant_cuisine_list:
            cuisine_combinations.append(tuple(variant_cuisine_list))
    
    # Group variants by cuisine combination
    combination_groups = defaultdict(list)
    for variant in restaurant_variants:
        combination_key = tuple(variant['cuisines'])
        combination_groups[combination_key].append(variant)
    
    # Calculate detailed statistics for each combination
    formatted_combinations = []
    for combination, variants_in_combo in combination_groups.items():
        # Price range statistics
        costs = [v['cost'] for v in variants_in_combo if v['cost'] > 0]
        price_range = {
            'min_price': min(costs) if costs else 0,
            'max_price': max(costs) if costs else 0,
            'average_price': sum(costs) / len(costs) if costs else 0
        }
        
        # Unique cuisine IDs statistics
        unique_cuisines = list(combination)
        cuisine_stats = {
            'unique_cuisine_ids': unique_cuisines,
            'total_cuisine_count': len(unique_cuisines)
        }
        
        # Menu items and categories statistics
        total_menu_items = sum(v['menu_items_count'] for v in variants_in_combo)
        all_categories = set()
        for variant in variants_in_combo:
            all_categories.update(variant['categories'])
        
        menu_stats = {
            'total_menu_items': total_menu_items,
            'total_unique_categories': len(all_categories),
            'category_names': [cat[1] for cat in all_categories if cat[1]]  # Extract category names
        }
        
        # Unique venues
        unique_venues = set(v['venue_id'] for v in variants_in_combo if v['venue_id'])
        venue_stats = {
            'total_unique_venues': len(unique_venues),
            'venue_ids': list(unique_venues)
        }
        
        # Free and paid services statistics - Updated to use proper service names
        all_free_services = []
        all_paid_services = []
        
        for variant in variants_in_combo:
            all_free_services.extend(variant['free_services'])
            all_paid_services.extend(variant['paid_services'])
        
        # Get unique service names
        unique_free_services = list(set(all_free_services))
        unique_paid_services = list(set(all_paid_services))
        
        service_stats = {
            'total_unique_free_services': len(unique_free_services),
            'free_service_names': unique_free_services,
            'total_unique_paid_services': len(unique_paid_services),
            'paid_service_names': unique_paid_services
        }
        
        # Matching variants info
        matching_variants = []
        for variant in variants_in_combo:
            matching_variants.append({
                'variant_id': variant['variant_id'],
                'variant_name': variant['variant_name'],
                'cost': variant['cost'],
                'venue_id': variant['venue_id'],
                'free_services_count': variant['free_services_count'],
                'paid_services_count': variant['paid_services_count']
            })
        
        formatted_combinations.append({
            'cuisine_combination': list(combination),
            'frequency': len(variants_in_combo),
            'combination_size': len(combination),
            'matching_variants': matching_variants,
            'price_range': price_range,
            'cuisine_stats': cuisine_stats,
            'menu_stats': menu_stats,
            'venue_stats': venue_stats,
            'service_stats': service_stats
        })
    
    # Sort combinations by frequency
    formatted_combinations.sort(key=lambda x: x['frequency'], reverse=True)
    
    restaurant_data = [{
        'restaurant_id': restaurant_id,
        'total_variants': len(variants),
        'variants': restaurant_variants
    }]
    
    all_cuisine_ids_list = sorted(list(all_cuisine_ids))
    
    return {
        'restaurant_data': restaurant_data,
        'sorted_cuisine_combinations': formatted_combinations,
        'all_cuisine_ids': all_cuisine_ids_list,
        'summary': {
            'total_variants': len(variants),
            'total_unique_cuisine_combinations': len(formatted_combinations),
            'total_unique_cuisines': len(all_cuisine_ids_list),
            'restaurant_id': restaurant_id
        }
    }

@app.route('/api/restaurant/cuisine-analysis', methods=['GET', 'POST'])
@handle_api_errors
def get_cuisine_analysis():
    """
    Get comprehensive cuisine analysis for restaurant variants
    """
    
    if request.method == 'POST':
        request_payload = request.get_json()
        if not request_payload:
            return jsonify({
                'error': 'Missing request payload',
                'message': 'POST request requires a JSON payload'
            }), 400
    else:
        request_payload = {}
        for key, value in request.args.items():
            if key not in ['include_summary', 'include_variants', 'include_combinations']:
                request_payload[key] = value
        
        if not request_payload:
            return jsonify({
                'error': 'Missing request parameters',
                'message': 'GET request requires query parameters for the external API'
            }), 400
    
    include_summary = request.args.get('include_summary', 'true').lower() == 'true'
    include_variants = request.args.get('include_variants', 'true').lower() == 'true'
    include_combinations = request.args.get('include_combinations', 'true').lower() == 'true'
    
    logger.info(f"Fetching variants data with payload: {request_payload}")
    api_response = fetch_variants_data(request_payload)
    
    parsed_data = parse_restaurant_variants(api_response)
    
    response_data = {}
    
    if include_summary:
        response_data['summary'] = parsed_data['summary']
    
    if include_variants:
        response_data['restaurant_data'] = parsed_data['restaurant_data']
    
    if include_combinations:
        response_data['sorted_cuisine_combinations'] = parsed_data['sorted_cuisine_combinations']
    
    response_data['all_cuisine_ids'] = parsed_data['all_cuisine_ids']
    
    logger.info(f"Successfully processed {parsed_data['summary']['total_variants']} variants")
    return jsonify(response_data)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': time.time(),
        'external_api_url': f"{config.BASE_URL}{config.ENDPOINT}"
    })

@app.route('/debug/config', methods=['GET'])
def debug_config():
    """Debug endpoint to check configuration"""
    return jsonify({
        'base_url': config.BASE_URL,
        'endpoint': config.ENDPOINT,
        'full_url': f"{config.BASE_URL}{config.ENDPOINT}",
        'timeout': config.TIMEOUT,
        'max_retries': config.MAX_RETRIES
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)