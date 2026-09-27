"""Legacy routes blueprint.

Endpoints:
- GET /health
- GET /entities
- GET /entity/<entity_id>/state
- POST /message
- POST /service/call
- POST /execute/automation
- POST /execute/script
- POST /conversation/clear
- POST /api/alexa/webhook
"""

import logging
import re as _re

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

legacy_bp = Blueprint('legacy', __name__)


@legacy_bp.route('/health', methods=['GET'])
def health():
    """Health check."""
    import api
    return jsonify({
        "status": "ok",
        "version": api.VERSION,
        "ai_provider": api.AI_PROVIDER,
        "ai_model": api.get_active_model(),
        "ai_configured": bool(api.get_api_key()),
        "ha_connected": bool(api.get_ha_token()),
    }), 200


@legacy_bp.route('/entities', methods=['GET'])
def get_entities_route():
    """Get all entities."""
    import api
    domain = request.args.get("domain", "")
    states = api.get_all_states()
    if domain:
        states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
    return jsonify({"entities": states, "count": len(states)}), 200


@legacy_bp.route('/entity/<entity_id>/state', methods=['GET'])
def get_entity_state_route(entity_id: str):
    """Get entity state."""
    import api
    return jsonify(api.call_ha_api("GET", f"states/{entity_id}")), 200


@legacy_bp.route('/message', methods=['POST'])
def send_message_legacy():
    """Legacy message endpoint."""
    import api
    data = request.get_json()
    return jsonify({"status": "success", "response": api.chat_with_ai(data.get("message", ""))}), 200


@legacy_bp.route('/service/call', methods=['POST'])
def call_service_route():
    """Call a Home Assistant service."""
    import api
    data = request.get_json()
    service = data.get("service", "")
    if not service or "." not in service:
        return jsonify({"error": "Use 'domain.service' format"}), 400
    domain, svc = service.split(".", 1)
    return jsonify(api.call_ha_api("POST", f"services/{domain}/{svc}", data.get("data", {}))), 200


@legacy_bp.route('/execute/automation', methods=['POST'])
def execute_automation():
    """Execute an automation."""
    import api
    data = request.get_json()
    eid = data.get("entity_id", data.get("automation_id", ""))
    if not eid.startswith("automation."):
        eid = f"automation.{eid}"
    return jsonify(api.call_ha_api("POST", "services/automation/trigger", {"entity_id": eid})), 200


@legacy_bp.route('/execute/script', methods=['POST'])
def execute_script():
    """Execute a script."""
    import api
    data = request.get_json()
    return jsonify(api.call_ha_api("POST", f"services/script/{data.get('script_id', '')}", data.get("variables", {}))), 200


@legacy_bp.route('/conversation/clear', methods=['POST'])
def clear_conversation():
    """Clear conversation history."""
    import api
    sid = (request.get_json() or {}).get("session_id", "default")
    api.conversations.pop(sid, None)
    api.session_active_skill.pop(sid, None)
    return jsonify({"status": "cleared"}), 200


@legacy_bp.route('/api/alexa/webhook', methods=['POST'])
def api_alexa_webhook():
    """Handle incoming Alexa Custom Skill requests.

    Supports:
    - LaunchRequest: greeting
    - IntentRequest with AskAmiraIntent: pass user speech to Amira
    - AMAZON.HelpIntent, AMAZON.StopIntent, AMAZON.CancelIntent
    - SessionEndedRequest: cleanup
    """
    import api
    data = request.get_json(silent=True) or {}
    req = data.get("request", {})
    req_type = req.get("type", "")
    session = data.get("session", {})
    session_id = f"alexa_{session.get('sessionId', 'default')}"

    logger.info(f"[Alexa] {req_type} session={session_id[:30]}")

    def _alexa_response(speech: str, end_session: bool = False, reprompt: str = None) -> dict:
        """Build an Alexa-compatible response envelope."""
        resp = {
            "version": "1.0",
            "sessionAttributes": session.get("attributes", {}),
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": speech,
                },
                "shouldEndSession": end_session,
            }
        }
        if reprompt:
            resp["response"]["reprompt"] = {
                "outputSpeech": {"type": "PlainText", "text": reprompt}
            }
        return resp

    try:
        if req_type == "LaunchRequest":
            greeting = {
                "it": "Ciao! Sono Amira, la tua assistente di casa. Chiedimi qualsiasi cosa!",
                "en": "Hi! I'm Amira, your home assistant. Ask me anything!",
                "es": "¡Hola! Soy Amira, tu asistente del hogar. ¡Pregúntame lo que quieras!",
                "fr": "Salut ! Je suis Amira, ton assistante maison. Demande-moi ce que tu veux !",
            }.get(api.LANGUAGE, "Hi! I'm Amira. Ask me anything!")
            return jsonify(_alexa_response(greeting, end_session=False, reprompt=greeting)), 200

        elif req_type == "IntentRequest":
            intent_name = req.get("intent", {}).get("name", "")

            if intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
                bye = {
                    "it": "Ciao ciao! A presto!",
                    "en": "Bye bye! See you soon!",
                    "es": "¡Adiós! ¡Hasta pronto!",
                    "fr": "Au revoir ! À bientôt !",
                }.get(api.LANGUAGE, "Bye!")
                return jsonify(_alexa_response(bye, end_session=True)), 200

            elif intent_name == "AMAZON.HelpIntent":
                help_text = {
                    "it": "Puoi chiedermi di controllare luci, temperatura, elettrodomestici o qualsiasi cosa sulla tua casa. Ad esempio: accendi la luce del salotto, che temperatura c'è in camera?",
                    "en": "You can ask me to control lights, temperature, appliances, or anything about your home. For example: turn on the living room light, what's the bedroom temperature?",
                    "es": "Puedes pedirme que controle luces, temperatura, electrodomésticos o cualquier cosa sobre tu casa.",
                    "fr": "Tu peux me demander de contrôler les lumières, la température, les appareils ou tout ce qui concerne ta maison.",
                }.get(api.LANGUAGE, "Ask me anything about your home!")
                return jsonify(_alexa_response(help_text, end_session=False)), 200

            elif intent_name == "AMAZON.FallbackIntent":
                fallback = {
                    "it": "Non ho capito. Puoi ripetere?",
                    "en": "I didn't understand. Can you repeat?",
                    "es": "No entendí. ¿Puedes repetir?",
                    "fr": "Je n'ai pas compris. Peux-tu répéter ?",
                }.get(api.LANGUAGE, "I didn't understand. Can you repeat?")
                return jsonify(_alexa_response(fallback, end_session=False, reprompt=fallback)), 200

            elif intent_name == "AskAmiraIntent":
                slots = req.get("intent", {}).get("slots", {})
                user_query = slots.get("query", {}).get("value", "")
                if not user_query:
                    prompt = {
                        "it": "Dimmi pure, cosa vuoi sapere?",
                        "en": "Go ahead, what would you like to know?",
                        "es": "Dime, ¿qué quieres saber?",
                        "fr": "Dis-moi, que veux-tu savoir ?",
                    }.get(api.LANGUAGE, "What would you like to know?")
                    return jsonify(_alexa_response(prompt, end_session=False, reprompt=prompt)), 200

                logger.info(f"[Alexa] AskAmiraIntent: {user_query[:80]}")

                try:
                    response_text = api.chat_with_ai(user_query, session_id)
                    response_text = _re.sub(r'```[\s\S]*?```', '', response_text)
                    response_text = _re.sub(r'`[^`]+`', '', response_text)
                    response_text = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', response_text)
                    response_text = _re.sub(r'[#*_~>|]', '', response_text)
                    response_text = _re.sub(r'\s+', ' ', response_text).strip()
                    if len(response_text) > 6000:
                        response_text = response_text[:6000] + "... Per il resto, puoi chiedermi di continuare."
                    logger.info(f"[Alexa] Response ({len(response_text)} chars): {response_text[:200]}")
                except Exception as e:
                    logger.error(f"[Alexa] AI error: {e}")
                    response_text = api.tr("alexa_ai_error", "Sorry, I couldn't process the response.")

                return jsonify(_alexa_response(
                    response_text,
                    end_session=False,
                    reprompt=api.tr("alexa_reprompt_anything_else", "Would you like to ask anything else?"),
                )), 200

            else:
                logger.warning(f"[Alexa] Unknown intent: {intent_name}")
                return jsonify(_alexa_response(
                    api.tr("alexa_unknown_intent", "I didn't understand the request."),
                    end_session=False,
                )), 200

        elif req_type == "SessionEndedRequest":
            logger.info(f"[Alexa] Session ended: {req.get('reason', 'unknown')}")
            return jsonify(_alexa_response("", end_session=True)), 200

        else:
            return jsonify(_alexa_response("", end_session=True)), 200

    except Exception as e:
        logger.error(f"[Alexa] Webhook error: {e}")
        return jsonify(_alexa_response(
            api.tr("alexa_generic_error_retry", "An error occurred. Please try again shortly."),
            end_session=False,
        )), 200
