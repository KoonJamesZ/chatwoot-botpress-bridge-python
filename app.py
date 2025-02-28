from flask import Flask, request, jsonify
import requests
import os
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*":{"origins":"*"}})

# Configuration
CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
BOTPRESS_BASE_URL = os.getenv("BOTPRESS_BASE_URL")
BOTPRESS_BOT_ID = os.getenv("BOTPRESS_BOT_ID")

# Headers for API requests
CHATWOOT_HEADERS = {
    'api_access_token': CHATWOOT_API_TOKEN,
    'Content-Type': 'application/json'
}

class ChatwootBotpressBridge:
    @staticmethod
    def send_to_botpress(message_content, conversation_id):
        """Send message to Botpress and get response"""
        try:
            botpress_url = f"{BOTPRESS_BASE_URL}/api/v1/bots/{BOTPRESS_BOT_ID}/converse/{conversation_id}"
            payload = {
                "type": "text",
                "text": message_content
            }
            response = requests.post(botpress_url, json=payload)
            response.raise_for_status()
            
            # Extract the first text response from Botpress
            botpress_data = response.json()
            if botpress_data.get('responses'):
                return botpress_data['responses'][0].get('text')
            return None
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Botpress API error: {str(e)}")
            return None

    @staticmethod
    def send_to_chatwoot(conversation_id, message):
        """Send message back to Chatwoot conversation"""
        try:
            chatwoot_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
            payload = {
                "content": message,
                "message_type": "outgoing"
            }
            response = requests.post(chatwoot_url, json=payload, headers=CHATWOOT_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Chatwoot API error: {str(e)}")
            return False
    
    @staticmethod
    def update_conversation_status(conversation_id, status="open"):
        """Update the status of a conversation (separate API call)"""
        try:
            toggle_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
            payload = {
                        "status": status
                        }
            response = requests.post(toggle_url, json=payload, headers=CHATWOOT_HEADERS)
            response.raise_for_status()
            return True
            
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Chatwoot conversation update error: {str(e)}")
            return False

# Initialize the bridge
bridge = ChatwootBotpressBridge()

# Webhook endpoint for Chatwoot
@app.route('/botpress', methods=['POST'])
def chatwoot_webhook():
    try:
        data = request.json
        # print(data)
        # Extract necessary information from the webhook payload
        message_type = data.get('message_type') # incoming (message from user) or outgoing (message from admin)
        content = data.get('content') # Message content
        conversation = data.get('conversation', {})# Conversation details
        conversation_id = conversation.get('id')
        # print(conversation_id)

        # Only process pending conversations (pending status means the conversation is not yet assigned to an agent)
        if conversation.get('status','') == "pending":
            # Only process incoming messages from user
            if message_type == 'incoming':
                # Get response from Botpress
                bot_response = bridge.send_to_botpress(content, conversation_id)
                
                # Handle the handoff case
                if bot_response == "handoff":
                    # First send the message to the user
                    handoff_message = "Please wait while I connect you to a human agent"
                    if bridge.send_to_chatwoot(conversation_id, handoff_message):
                        # Then update the conversation status to open
                        if bridge.update_conversation_status(conversation_id, "open"):
                            return jsonify({"status": "success", "message": "Handoff processed successfully"}), 200
                        else:
                            return jsonify({"status": "error", "message": "Failed to update conversation status"}), 500
                    else:
                        return jsonify({"status": "error", "message": "Failed to send handoff message"}), 500
                
                # Handle normal bot responses
                if not bot_response:
                    return jsonify({"status": "error", "message": "Failed to get Botpress response"}), 500

                # Send the response back to Chatwoot
                if bridge.send_to_chatwoot(conversation_id, bot_response):
                    return jsonify({"status": "success", "message": "Message processed successfully"}), 200
                else:
                    return jsonify({"status": "error", "message": "Failed to send message to Chatwoot"}), 500
            
            # Only process outgoing messages from admin (message_type = outgoing)
            else:
                return jsonify({"status": "ignored", "reason": "not an incoming message"}), 200
            
        else:
            return jsonify({"status": "success", "message": "Message processed successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    # Load configuration
    port = int(os.getenv('PORT', 3100))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    # Start the Flask application
    app.run(host='0.0.0.0', port=port, debug=debug)