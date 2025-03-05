from flask import Flask, request, jsonify
import requests
import os
from flask_cors import CORS
from dotenv import load_dotenv
import json

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuration
CHATWOOT_ADMIN_API_TOKEN = os.getenv("CHATWOOT_ADMIN_API_TOKEN")
CHATWOOT_BOT_API_TOKEN = os.getenv("CHATWOOT_BOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")  # New environment variable for inbox ID
BOTPRESS_BASE_URL = os.getenv("BOTPRESS_BASE_URL")
BOTPRESS_BOT_ID = os.getenv("BOTPRESS_BOT_ID")

# Headers for API requests
CHATWOOT_ADMIN_HEADERS = {
    'api_access_token': CHATWOOT_ADMIN_API_TOKEN,
    'Content-Type': 'application/json'
}

CHATWOOT_BOT_HEADERS = {
    'api_access_token': CHATWOOT_BOT_API_TOKEN,
    'Content-Type': 'application/json'
}

# Global variable to store the last assigned agent index for round-robin selection
last_assigned_agent_index = -1

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
            response = requests.post(chatwoot_url, json=payload, headers=CHATWOOT_BOT_HEADERS)
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
            payload = {"status": status}
            response = requests.post(toggle_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Chatwoot conversation update error: {str(e)}")
            return False
    
    @staticmethod
    def assign_conversation_to_bot(conversation_id):
        """Assign the conversation to the bot (i.e. unassign any human agent)"""
        try:
            assignment_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/assignments"
            payload = {"assignee_id": None}  # None indicates bot assignment
            response = requests.post(assignment_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Chatwoot assignment update error: {str(e)}")
            return False

    @staticmethod
    def get_available_human_agent():
        """Fetch available inbox members filtered by online status using the inbox_members endpoint and return one using round-robin."""
        global last_assigned_agent_index
        try:
            # Use the provided endpoint to get members for the inbox.
            members_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/inbox_members/{CHATWOOT_INBOX_ID}"
            response = requests.get(members_url, headers=CHATWOOT_ADMIN_HEADERS)
            members = response.json().get('payload', []) # Expected to be a list of inbox member objects
            # Filter members by online status: check for 'is_online' or 'status' == "online"
            
            online_members = []
            for member in members:
                if member.get('availability_status', '').lower() == 'online':
                    online_members.append(member)
            
            # Fallback to all members if none are online
            candidate_members = online_members if online_members else members
            if not candidate_members:
                app.logger.error("No members available for assignment")
                return None

            # Round-robin selection using global index
            last_assigned_agent_index = (last_assigned_agent_index + 1) % len(candidate_members)
            return candidate_members[last_assigned_agent_index].get('id')
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Error fetching inbox members: {str(e)}")
            return None

    @staticmethod
    def assign_conversation_to_human(conversation_id):
        """Assign conversation to an available human agent using filtering logic on inbox members."""
        human_agent_id = ChatwootBotpressBridge.get_available_human_agent()
        if human_agent_id is None:
            app.logger.error("No available human agent found")
            return False
        try:
            assignment_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/assignments"
            payload = {"assignee_id": human_agent_id}
            response = requests.post(assignment_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Chatwoot assignment update error: {str(e)}")
            return False

# Initialize the bridge
bridge = ChatwootBotpressBridge()

# Webhook endpoint for Chatwoot
@app.route('/botpress', methods=['POST'])
def chatwoot_webhook():
    try:
        data = request.json
        # print(json.dumps(data,indent=4,ensure_ascii=False))
        message_type = data.get("message_type", "")  # incoming or outgoing
        content = data.get("content", "")
        conversation = data.get("conversation", {})
        conversation_id = conversation.get("id", "")
        conversation_assignee = conversation.get("meta", {}).get("assignee", "")

        # If conversation is resolved, assign to bot
        if data.get('event', '') == "conversation_resolved":
            conversation_id = data.get('id', '')
            bridge.assign_conversation_to_bot(conversation_id)
            return jsonify({"status": "success", "message": "Conversation resolved"}), 200
        
        # When status is pending, change to open and assign to bot
        if conversation.get('status', '') == "pending":
            bridge.update_conversation_status(conversation_id, "open")

        # Process only incoming messages from user
        if message_type == 'incoming' and conversation_assignee is None:
            bot_response = bridge.send_to_botpress(content, conversation_id)
            
            # Handle handoff: if Botpress returns "handoff", notify user and assign to a human agent
            if bot_response == "handoff":
                handoff_message = "Please wait while I connect you to a human agent"
                if bridge.send_to_chatwoot(conversation_id, handoff_message):
                    if bridge.assign_conversation_to_human(conversation_id):
                        return jsonify({"status": "success", "message": "Handoff processed successfully"}), 200
                    else:
                        return jsonify({"status": "error", "message": "Failed to update conversation assignment"}), 500
                else:
                    return jsonify({"status": "error", "message": "Failed to send handoff message"}), 500
            
            if not bot_response:
                return jsonify({"status": "error", "message": "Failed to get Botpress response"}), 500

            if bridge.send_to_chatwoot(conversation_id, bot_response):
                return jsonify({"status": "success", "message": "Message processed successfully"}), 200
            else:
                return jsonify({"status": "error", "message": "Failed to send message to Chatwoot"}), 500
        
        # Ignore outgoing messages from admin
        else:
            return jsonify({"status": "ignored", "reason": "not an incoming message"}), 200
            
    except Exception as e:
        app.logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3100))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
