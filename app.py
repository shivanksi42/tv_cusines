from flask import Flask, jsonify, request
import requests
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional
import logging
from functools import wraps
import time

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Config:
 
    BASE_URL = "https://api.staging.tracevenue.com"
    ENDPOINT = "/api/v1/traceVenue/variant/filteredVariants"
    TIMEOUT = 30 
    MAX_RETRIES = 3

config = Config()

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

def fetch_variants_data(request_payload: Optional[Dict] = None, method: str = 'POST') -> Dict[str, Any]:
    """
    Fetch variants data from the external API
    
    Args:
        request_payload: Request payload to send to the API
        method: HTTP method to use (GET or POST)
        
    Returns:
        API response data
    """
    url = f"{config.BASE_URL}{config.ENDPOINT}"
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        
    }
    

    if request_payload is None:
        request_payload = {
            "locations": ["Mohali"],
            "radius": 10,
            "eventTypeId": "67bc5e973bc3df51e0c50a41",
            "eventName": "Retirement party pack",
            "minPerson": 450,
            "maxPerson": 500,
            "latitude": 30.7215043,
            "longitude": 76.7026142
        }
 
    method = 'POST'
    

    for attempt in range(config.MAX_RETRIES):
        try:
            logger.info(f"Fetching data from {url} (attempt {attempt + 1})")
            logger.info(f"Method: {method}")
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

def parse_restaurant_variants(api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse restaurant variants data to extract cuisine combinations and related information.
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
    
    for variant in variants:
        variant_id = variant.get('_id', 'unknown')
        variant_name = variant.get('name', 'unnamed')
        menu_items = variant.get('menuItems', [])
        
        variant_cuisines = set()
        
        for menu_item in menu_items:
            cuisines = menu_item.get('cuisine', [])
            for cuisine_id in cuisines:
                variant_cuisines.add(cuisine_id)
                all_cuisine_ids.add(cuisine_id)
        
        variant_cuisine_list = sorted(list(variant_cuisines))
        
        restaurant_variants.append({
            'variant_id': variant_id,
            'variant_name': variant_name,
            'cuisines': variant_cuisine_list,
            'cuisine_count': len(variant_cuisine_list),
            'menu_items_count': len(menu_items),
            'cost': variant.get('cost', 0),
            'min_persons': variant.get('minPersons', 0),
            'max_persons': variant.get('maxPersons', 0),
            'is_customized': variant.get('isCustomized', False)
        })
        
        if variant_cuisine_list:
            cuisine_combinations.append(tuple(variant_cuisine_list))
    
    combination_counter = Counter(cuisine_combinations)
    sorted_combinations = combination_counter.most_common()
    
    formatted_combinations = []
    for combination, count in sorted_combinations:
        matching_variants = []
        for variant in restaurant_variants:
            if tuple(variant['cuisines']) == combination:
                matching_variants.append({
                    'variant_id': variant['variant_id'],
                    'variant_name': variant['variant_name'],
                    'cost': variant['cost']
                })
        
        formatted_combinations.append({
            'cuisine_combination': list(combination),
            'frequency': count,
            'matching_variants': matching_variants,
            'combination_size': len(combination)
        })
    
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
    Get cuisine analysis for restaurant variants
    
    For POST requests, send the payload in request body
    For GET requests, parameters are converted to payload
    
    Query Parameters (GET) or Body Parameters (POST):
    - restaurant_id: Optional restaurant ID to filter by
    - page: Page number for pagination
    - limit: Number of results per page
    - Any other parameters your external API expects
    """
    
    if request.method == 'POST':
        request_payload = request.get_json() or {}
        method = 'POST'
    else:
        request_payload = {}
        for key, value in request.args.items():
            if key not in ['include_summary', 'include_variants', 'include_combinations']:
                request_payload[key] = value
        method = 'GET'
    
    include_summary = request.args.get('include_summary', 'true').lower() == 'true'
    include_variants = request.args.get('include_variants', 'true').lower() == 'true'
    include_combinations = request.args.get('include_combinations', 'true').lower() == 'true'
    
    logger.info(f"Fetching variants data with payload: {request_payload}")
    api_response = fetch_variants_data(request_payload, method)
    
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

def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': time.time(),
        'external_api_url': f"{config.BASE_URL}{config.ENDPOINT}"
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)