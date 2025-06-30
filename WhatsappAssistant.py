import os
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import openai
from datetime import datetime
import json
import redis
from typing import Dict
import logging

# Initialize Flask app
app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')

# Initialize clients
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai.api_key = OPENAI_API_KEY
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Supported languages
LANGUAGE_MAP = {
    'en': 'English',
    'ar': 'Arabic',
    'fr': 'French',
    'sw': 'Swahili',
    'ha': 'Hausa',
    'yo': 'Yoruba',
    'am': 'Amharic',
    'zu': 'Zulu',
    'pt': 'Portuguese'
}

class ConversationManager:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.context_ttl = 3600

    def get_context(self, phone_number: str) -> Dict:
        key = f"context:{phone_number}"
        context = self.redis.get(key)
        if context:
            return json.loads(context)
        return {"messages": [], "language": "en", "business_context": {}}

    def update_context(self, phone_number: str, message: str, response: str, language: str = None):
        context = self.get_context(phone_number)
        context["messages"].append({
            "timestamp": datetime.now().isoformat(),
            "user": message,
            "assistant": response
        })
        context["messages"] = context["messages"][-10:]
        if language:
            context["language"] = language
        self.redis.setex(f"context:{phone_number}", self.context_ttl, json.dumps(context))

class BusinessContext:
    def __init__(self, business_id: str):
        self.business_id = business_id
        self.load_business_info()

    def load_business_info(self):
        self.info = {
            "name": "Demo Store",
            "products": [
                {"id": 1, "name": "Product A", "price": 100, "description": "High quality product A"},
                {"id": 2, "name": "Product B", "price": 200, "description": "Premium product B"}
            ],
            "faq": {
                "shipping": "We ship within 24 hours across the country",
                "payment": "We accept mobile money, bank transfer, and cash on delivery",
                "returns": "30-day return policy on all items"
            },
            "working_hours": "Monday-Saturday, 9AM-6PM"
        }

    def get_system_prompt(self, language: str) -> str:
        return f"""You are a helpful sales assistant for {self.info['name']}. 
Respond in {LANGUAGE_MAP.get(language, 'English')}.
Products: {json.dumps(self.info['products'])}
FAQ: {json.dumps(self.info['faq'])}
Working Hours: {self.info['working_hours']}
Be friendly, concise, and helpful. Keep it short for WhatsApp."""

# Initialize
conv_manager = ConversationManager(redis_client)

def detect_language(text: str) -> str:
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Detect the language of the text and return only the ISO code like 'en'."},
                {"role": "user", "content": text}
            ],
            max_tokens=10,
            temperature=0
        )
        lang_code = response.choices[0].message.content.strip().lower()
        return lang_code if lang_code in LANGUAGE_MAP else 'en'
    except Exception as e:
        logger.error(f"Language detection failed: {e}")
        return 'en'

def generate_response(message: str, phone_number: str, business_id: str = "demo") -> str:
    try:
        context = conv_manager.get_context(phone_number)
        if not context.get("language") or len(context["messages"]) == 0:
            context["language"] = detect_language(message)

        business = BusinessContext(business_id)

        messages = [{"role": "system", "content": business.get_system_prompt(context["language"])}]
        for msg in context["messages"][-5:]:
            messages.append({"role": "user", "content": msg["user"]})
            messages.append({"role": "assistant", "content": msg["assistant"]})
        messages.append({"role": "user", "content": message})

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=200,
            temperature=0.7
        )

        ai_response = response.choices[0].message.content
        conv_manager.update_context(phone_number, message, ai_response, context["language"])
        return ai_response
    except Exception as e:
        logger.error(f"Error generating response: {e}")
        return "Sorry, I'm having trouble responding. Please try again later."

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        from_number = request.values.get('From', '')
        logger.info(f"From {from_number}: {incoming_msg}")
        response_text = generate_response(incoming_msg, from_number)
        resp = MessagingResponse()
        resp.message(response_text)
        return str(resp)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        resp = MessagingResponse()
        resp.message("An error occurred. Please try again.")
        return str(resp)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
