from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import os
import tempfile
import time

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all domains

# Configuration
CHATWOOT_ADMIN_API_TOKEN = os.getenv("CHATWOOT_ADMIN_API_TOKEN")
CHATWOOT_BOT_API_TOKEN = os.getenv("CHATWOOT_BOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")
BOTPRESS_BASE_URL = os.getenv("BOTPRESS_BASE_URL")
BOTPRESS_BOT_ID = os.getenv("BOTPRESS_BOT_ID")

# Headers for API requests
CHATWOOT_ADMIN_HEADERS = {
    "api_access_token": CHATWOOT_ADMIN_API_TOKEN,
    "Content-Type": "application/json"
}

CHATWOOT_BOT_HEADERS = {
    "api_access_token": CHATWOOT_BOT_API_TOKEN,
    "Content-Type": "application/json"
}

# Global variable for round-robin human agent assignment
last_assigned_agent_index = -1

class ChatwootBotpressBridge:
    @staticmethod
    def send_to_botpress(message_content, conversation_id):
        """
        Send a message to Botpress and return the full JSON response.
        """
        try:
            botpress_url = f"{BOTPRESS_BASE_URL}/api/v1/bots/{BOTPRESS_BOT_ID}/converse/{conversation_id}"
            payload = {"type": "text", "text": message_content}
            response = requests.post(botpress_url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Botpress API error: {str(e)}")

    @staticmethod
    def send_to_chatwoot(conversation_id, message):
        """
        Send a text message to a Chatwoot conversation.
        """
        try:
            chatwoot_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
            payload = {"content": message, "message_type": "outgoing"}
            response = requests.post(chatwoot_url, json=payload, headers=CHATWOOT_BOT_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise Exception(f"Chatwoot API error: {str(e)}")

    @staticmethod
    def update_conversation_status(conversation_id, status="open"):
        """
        Update the status of a conversation.
        """
        try:
            toggle_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
            payload = {"status": status}
            response = requests.post(toggle_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise Exception(f"Chatwoot conversation update error: {str(e)}")

    @staticmethod
    def assign_conversation_to_bot(conversation_id):
        """
        Assign the conversation to the bot (i.e. unassign any human agent).
        """
        try:
            assignment_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/assignments"
            payload = {"assignee_id": None}  # None indicates bot assignment
            response = requests.post(assignment_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise Exception(f"Chatwoot assignment update error: {str(e)}")

    @staticmethod
    def get_available_human_agent():
        """
        Fetch available inbox members and return one using round-robin selection.
        """
        global last_assigned_agent_index
        try:
            members_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/inbox_members/{CHATWOOT_INBOX_ID}"
            response = requests.get(members_url, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            members = response.json().get("payload", [])
            if not members:
                raise Exception("No inbox members found")
            last_assigned_agent_index = (last_assigned_agent_index + 1) % len(members)
            return members[last_assigned_agent_index].get("id")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Error fetching inbox members: {str(e)}")

    @staticmethod
    def assign_conversation_to_human(conversation_id):
        """
        Assign the conversation to an available human agent.
        """
        human_agent_id = ChatwootBotpressBridge.get_available_human_agent()
        if human_agent_id is None:
            raise Exception("No available human agent found")
        try:
            assignment_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/assignments"
            payload = {"assignee_id": human_agent_id}
            response = requests.post(assignment_url, json=payload, headers=CHATWOOT_ADMIN_HEADERS)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise Exception(f"Chatwoot assignment update error: {str(e)}")

    @staticmethod
    def send_attachment_to_chatwoot(conversation_id, file_path, file_name, mime_type="application/octet-stream"):
        """
        Send a file attachment to Chatwoot using a multipart/form-data request.
        """
        chatwoot_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
        with open(file_path, "rb") as f:
            files = {"attachments[]": (file_name, f, mime_type)}
            data = {"message_type": "outgoing"}
            headers = {"api_access_token": CHATWOOT_BOT_API_TOKEN}
            response = requests.post(chatwoot_url, data=data, files=files, headers=headers)
            response.raise_for_status()
            return response.json()

# Initialize the bridge
bridge = ChatwootBotpressBridge()

@app.route("/botpress", methods=["POST"])
def chatwoot_webhook():
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            raise Exception("Invalid data format: expected a JSON object")

        message_type = data.get("message_type", "")
        content = data.get("content", "")
        conversation = data.get("conversation", {})
        conversation_id = conversation.get("id", "")
        conversation_assignee = conversation.get("meta", {}).get("assignee", "")

        # If conversation is resolved, assign to bot
        if data.get("event", "") == "conversation_resolved":
            conversation_id = data.get("id", "")
            bridge.assign_conversation_to_bot(conversation_id)
            return jsonify({"status": "success", "message": "Conversation resolved"}), 200

        # When status is pending, update it to open
        if conversation.get("status", "") == "pending":
            bridge.update_conversation_status(conversation_id, "open")

        # Process only incoming messages from the user
        if message_type == "incoming" and conversation_assignee is None:
            bot_response = bridge.send_to_botpress(content, conversation_id)

            # Check if the Botpress response contains file attachment(s)
            if isinstance(bot_response, dict) and bot_response.get("responses"):
                bp_resp = bot_response["responses"][0]
                if bp_resp.get("type") in ["file", "image", "audio", "video"]:
                    file_url = bp_resp.get(bp_resp.get("type"))
                    title = bp_resp.get("title", "attachment")
                    # Download the file from Botpress
                    file_download = requests.get(file_url)
                    if file_download.status_code != 200:
                        raise Exception("Failed to download file from Botpress")
                    file_content = file_download.content
                    mime_type = file_download.headers.get("Content-Type", "application/octet-stream")
                    filename = os.path.basename(file_url)
                    # Write file content to a temporary file
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(file_content)
                        tmp.flush()
                        temp_file_path = tmp.name
                    # Send the downloaded file as an attachment to Chatwoot
                    bridge.send_attachment_to_chatwoot(conversation_id, temp_file_path, filename, mime_type)
                    os.remove(temp_file_path)
                    return jsonify({"status": "success", "message": "Attachment sent successfully"}), 200
                else:
                    # Handle non-file (text) response from Botpress
                    bot_text = bp_resp.get("text")
                    if bot_text == "handoff":
                        handoff_message = "Please wait while I connect you to a human agent"
                        if bridge.send_to_chatwoot(conversation_id, handoff_message):
                            if bridge.assign_conversation_to_human(conversation_id):
                                return jsonify({"status": "success", "message": "Handoff processed successfully"}), 200
                            else:
                                raise Exception("Failed to update conversation assignment")
                        else:
                            raise Exception("Failed to send handoff message")
                    if not bot_text:
                        raise Exception("Failed to get Botpress response")
                    if bridge.send_to_chatwoot(conversation_id, bot_text):
                        return jsonify({"status": "success", "message": "Message processed successfully"}), 200
                    else:
                        raise Exception("Failed to send message to Chatwoot")
            else:
                raise Exception("Invalid Botpress response format")
        else:
            # Ignore outgoing or non-user messages
            return jsonify({"status": "ignored", "reason": "not an incoming message"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3100))
    app.run(host="0.0.0.0", port=port)
