#!/usr/bin/env python3
"""
Voice Transcription Module (Groq Whisper + Fallback Providers)

Supports:
- Groq Whisper (primary)
- OpenAI Whisper (fallback)
- Google Speech-to-Text (fallback)

Also provides:
- Text-to-Speech (Groq, OpenAI, Google)
- Audio format detection and conversion
"""

import os
import io
import json
import base64
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, Any, List
from pathlib import Path
from enum import Enum
import logging

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Edge TTS voice mapping per language
EDGE_TTS_VOICES = {
    "it": "it-IT-IsabellaNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
}

EDGE_TTS_VOICES_MALE = {
    "it": "it-IT-DiegoNeural",
    "en": "en-US-GuyNeural",
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
}


class AudioFormat(Enum):
    """Supported audio formats."""
    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"
    FLAC = "flac"
    M4A = "m4a"
    WEBM = "webm"
    AAC = "aac"


class TranscriptionProvider(Enum):
    """Available transcription providers."""
    GROQ = "groq"
    OPENAI = "openai"
    GOOGLE = "google"


class VoiceTranscriber:
    """Transcribe audio to text."""
    
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.language = (os.getenv("LANGUAGE", "en") or "en").lower()[:2]
        self.transcription_cache: Dict[str, Dict[str, Any]] = {}
        self.provider_order = [
            TranscriptionProvider.GROQ,
            TranscriptionProvider.OPENAI,
            TranscriptionProvider.GOOGLE,
        ]

    def _transcription_language(self) -> str:
        return self.language if self.language in ("en", "it", "es", "fr") else "en"

    def _google_language_code(self) -> str:
        return {
            "it": "it-IT",
            "en": "en-US",
            "es": "es-ES",
            "fr": "fr-FR",
        }.get(self._transcription_language(), "en-US")
    
    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds (approximate)."""
        try:
            size = os.path.getsize(audio_path)
            # Rough estimate: bitrate ~128kbps = 16000 bytes per second
            return size / 16000
        except:
            return 0.0
    
    def _get_audio_format(self, audio_path: str) -> Optional[AudioFormat]:
        """Detect audio format from file extension."""
        ext = Path(audio_path).suffix.lower().lstrip(".")
        for fmt in AudioFormat:
            if fmt.value == ext:
                return fmt
        return None
    
    def transcribe_with_groq(self, audio_path: str) -> Tuple[bool, str]:
        """Transcribe using Groq Whisper API."""
        if not self.groq_api_key:
            return False, "Groq API key not configured"
        
        try:
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            
            audio_fmt = self._get_audio_format(audio_path)
            mime = f"audio/{audio_fmt.value}" if audio_fmt else "audio/wav"
            filename = Path(audio_path).name
            
            with open(audio_path, "rb") as audio_file:
                files = {
                    "file": (filename, audio_file, mime),
                }
                data = {
                    "model": "whisper-large-v3-turbo",
                    "language": self._transcription_language(),
                }
                headers = {"Authorization": f"Bearer {self.groq_api_key}"}
                
                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "")
                    logger.info(f"Groq transcription: {len(text)} chars")
                    return True, text
                else:
                    error = response.json().get("error", {}).get("message", "Unknown error")
                    return False, f"Groq error: {error}"
        except Exception as e:
            logger.error(f"Groq transcription error: {e}")
            return False, str(e)
    
    def transcribe_with_openai(self, audio_path: str) -> Tuple[bool, str]:
        """Transcribe using OpenAI Whisper API."""
        if not self.openai_api_key:
            return False, "OpenAI API key not configured"
        
        try:
            url = "https://api.openai.com/v1/audio/transcriptions"
            
            audio_fmt = self._get_audio_format(audio_path)
            mime = f"audio/{audio_fmt.value}" if audio_fmt else "audio/wav"
            filename = Path(audio_path).name
            
            with open(audio_path, "rb") as audio_file:
                files = {
                    "file": (filename, audio_file, mime),
                }
                data = {
                    "model": "whisper-1",
                    "language": self._transcription_language(),
                }
                headers = {"Authorization": f"Bearer {self.openai_api_key}"}
                
                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "")
                    logger.info(f"OpenAI transcription: {len(text)} chars")
                    return True, text
                else:
                    error = response.json().get("error", {}).get("message", "Unknown error")
                    return False, f"OpenAI error: {error}"
        except Exception as e:
            logger.error(f"OpenAI transcription error: {e}")
            return False, str(e)
    
    def transcribe_with_google(self, audio_path: str) -> Tuple[bool, str]:
        """Transcribe using Google Speech-to-Text API."""
        if not self.google_api_key:
            return False, "Google API key not configured"
        
        try:
            url = f"https://speech.googleapis.com/v1/speech:recognize?key={self.google_api_key}"
            
            # Read audio file
            with open(audio_path, "rb") as f:
                audio_content = base64.b64encode(f.read()).decode("utf-8")
            
            payload = {
                "config": {
                    "encoding": "LINEAR16",
                    "languageCode": self._google_language_code(),
                    "model": "latest_long",
                },
                "audio": {
                    "content": audio_content,
                }
            }
            
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                results = result.get("results", [])
                # Concatenate all transcriptions
                text = " ".join(
                    alt.get("transcript", "")
                    for result in results
                    for alt in result.get("alternatives", [])
                )
                logger.info(f"Google transcription: {len(text)} chars")
                return True, text
            else:
                error = response.json().get("error", {}).get("message", "Unknown error")
                return False, f"Google error: {error}"
        except Exception as e:
            logger.error(f"Google transcription error: {e}")
            return False, str(e)
    
    def transcribe_with_fallback(self, audio_path: str) -> Tuple[bool, str, str]:
        """
        Transcribe with automatic fallback between providers.
        Returns: (success, text, provider_used)
        """
        if not os.path.exists(audio_path):
            return False, "", "none"
        
        # Check cache first
        audio_hash = self._hash_file(audio_path)
        if audio_hash in self.transcription_cache:
            cached = self.transcription_cache[audio_hash]
            if datetime.fromisoformat(cached["timestamp"]) > datetime.now() - timedelta(hours=24):
                logger.info(f"Transcription cache hit")
                return True, cached["text"], f"{cached['provider']} (cached)"
        
        # Try providers in order
        for provider in self.provider_order:
            if provider == TranscriptionProvider.GROQ:
                success, text = self.transcribe_with_groq(audio_path)
            elif provider == TranscriptionProvider.OPENAI:
                success, text = self.transcribe_with_openai(audio_path)
            elif provider == TranscriptionProvider.GOOGLE:
                success, text = self.transcribe_with_google(audio_path)
            else:
                continue
            
            if success:
                # Cache result
                self.transcription_cache[audio_hash] = {
                    "text": text,
                    "provider": provider.value,
                    "timestamp": datetime.now().isoformat(),
                    "duration_seconds": self._get_audio_duration(audio_path),
                }
                logger.info(f"Transcription successful with {provider.value}")
                return True, text, provider.value
        
        return False, "All transcription providers failed", "none"
    
    @staticmethod
    def _hash_file(filepath: str) -> str:
        """Get SHA256 hash of file."""
        import hashlib
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            sha256_hash.update(f.read())
        return sha256_hash.hexdigest()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get transcription statistics."""
        return {
            "cache_size": len(self.transcription_cache),
            "providers_available": {
                "groq": bool(self.groq_api_key),
                "openai": bool(self.openai_api_key),
                "google": bool(self.google_api_key),
            },
            "cached_transcriptions": [
                {
                    "provider": v["provider"],
                    "duration": v["duration_seconds"],
                    "timestamp": v["timestamp"]
                }
                for v in list(self.transcription_cache.values())[-5:]
            ]
        }


class TextToSpeech:
    """Convert text to speech with Edge TTS, Groq, OpenAI, Google fallback."""
    
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.language = os.getenv("LANGUAGE", "en").lower()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _google_language_code(self) -> str:
        lang = (self.language or "en").lower()[:2]
        return {
            "it": "it-IT",
            "en": "en-US",
            "es": "es-ES",
            "fr": "fr-FR",
        }.get(lang, "en-US")
    
    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create an event loop for async edge-tts calls."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
                if self._loop.is_closed():
                    raise RuntimeError
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def speak_with_edge(self, text: str, voice: str = "") -> Tuple[bool, bytes]:
        """Generate speech using Edge TTS (free, no API key, supports Italian)."""
        if not EDGE_TTS_AVAILABLE:
            logger.warning("Edge TTS not available — edge-tts package not installed")
            return False, b""
        
        try:
            # Select voice based on config value and language
            voice_lower = (voice or "").lower().strip()
            if not voice_lower or voice_lower == "female" or voice_lower in ("nova", "alloy", "echo", "fable", "onyx", "shimmer"):
                # female (default) → auto-select female voice for current language
                edge_voice = EDGE_TTS_VOICES.get(self.language, EDGE_TTS_VOICES["en"])
            elif voice_lower == "male":
                # male → auto-select male voice for current language
                edge_voice = EDGE_TTS_VOICES_MALE.get(self.language, EDGE_TTS_VOICES_MALE["en"])
            else:
                # Direct Edge TTS voice name (e.g. it-IT-ElsaNeural) for power users
                edge_voice = voice
            
            async def _generate():
                communicate = edge_tts.Communicate(text, edge_voice)
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]
                return audio_data
            
            loop = self._get_event_loop()
            audio_bytes = loop.run_until_complete(_generate())
            
            if audio_bytes:
                logger.info(f"Edge TTS ({edge_voice}): {len(text)} chars -> {len(audio_bytes)} bytes")
                return True, audio_bytes
            else:
                logger.warning("Edge TTS returned empty audio")
                return False, b""
        except Exception as e:
            logger.error(f"Edge TTS error: {e}")
            return False, b""
    
    def speak_with_groq(self, text: str, voice: str = "autumn") -> Tuple[bool, bytes]:
        """Generate speech using Groq TTS (Orpheus model).
        
        Note: Groq TTS has 200 char limit per request. This method
        automatically chunks longer text.
        Officially English + Arabic only, but may work with other languages.
        """
        if not self.groq_api_key:
            return False, b""
        
        # Map OpenAI voice names to Groq Orpheus voices
        groq_voice_map = {
            "nova": "autumn", "alloy": "diana", "echo": "troy",
            "fable": "hannah", "onyx": "daniel", "shimmer": "austin",
        }
        groq_valid_voices = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]
        resolved_voice = groq_voice_map.get(voice, voice)
        if resolved_voice not in groq_valid_voices:
            resolved_voice = "autumn"
        
        try:
            url = "https://api.groq.com/openai/v1/audio/speech"
            headers = {
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
            }
            
            # Chunk text if > 200 chars (Groq limit)
            chunks = self._chunk_text(text, max_chars=200)
            all_audio = b""
            
            for chunk in chunks:
                payload = {
                    "model": "playai/playht-tts-v3",
                    "input": chunk,
                    "voice": resolved_voice,
                    "response_format": "wav",
                }
                
                response = requests.post(
                    url, json=payload, headers=headers, timeout=30
                )
                
                if response.status_code == 200:
                    all_audio += response.content
                else:
                    error_msg = ""
                    try:
                        error_msg = response.json().get("error", {}).get("message", "")
                    except:
                        error_msg = response.text[:200]
                    logger.warning(f"Groq TTS error ({response.status_code}): {error_msg}")
                    return False, b""
            
            if all_audio:
                logger.info(f"Groq TTS ({resolved_voice}): {len(text)} chars -> {len(all_audio)} bytes")
                return True, all_audio
            return False, b""
        except Exception as e:
            logger.error(f"Groq TTS error: {e}")
            return False, b""
    
    @staticmethod
    def _chunk_text(text: str, max_chars: int = 200) -> List[str]:
        """Split text into chunks respecting sentence boundaries."""
        if len(text) <= max_chars:
            return [text]
        
        chunks = []
        sentences = []
        # Split on sentence-ending punctuation
        current = ""
        for char in text:
            current += char
            if char in ".!?;" and len(current.strip()) > 0:
                sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())
        
        current_chunk = ""
        for sentence in sentences:
            if len(current_chunk) + len(sentence) + 1 <= max_chars:
                current_chunk = (current_chunk + " " + sentence).strip()
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # If a single sentence exceeds max, split by words
                if len(sentence) > max_chars:
                    words = sentence.split()
                    sub_chunk = ""
                    for word in words:
                        if len(sub_chunk) + len(word) + 1 <= max_chars:
                            sub_chunk = (sub_chunk + " " + word).strip()
                        else:
                            if sub_chunk:
                                chunks.append(sub_chunk)
                            sub_chunk = word
                    if sub_chunk:
                        current_chunk = sub_chunk
                else:
                    current_chunk = sentence
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks if chunks else [text[:max_chars]]
    
    def speak_with_openai(self, text: str, voice: str = "nova") -> Tuple[bool, bytes]:
        """Generate speech using OpenAI TTS."""
        if not self.openai_api_key:
            return False, b""
        
        try:
            url = "https://api.openai.com/v1/audio/speech"
            
            payload = {
                "model": "tts-1",
                "input": text,
                "voice": voice,
            }
            headers = {"Authorization": f"Bearer {self.openai_api_key}"}
            
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info(f"OpenAI TTS: {len(text)} chars -> audio")
                return True, response.content
            else:
                return False, b""
        except Exception as e:
            logger.error(f"OpenAI TTS error: {e}")
            return False, b""
    
    def speak_with_google(self, text: str, language: Optional[str] = None) -> Tuple[bool, bytes]:
        """Generate speech using Google TTS."""
        if not self.google_api_key:
            return False, b""
        
        try:
            url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self.google_api_key}"
            language_code = (language or self._google_language_code()).strip() or "en-US"
            
            payload = {
                "input": {"text": text},
                "voice": {
                    "languageCode": language_code,
                    "name": f"{language_code}-Neural2-A",
                },
                "audioConfig": {
                    "audioEncoding": "MP3",
                }
            }
            
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                audio_content = base64.b64decode(result["audioContent"])
                logger.info(f"Google TTS: {len(text)} chars -> audio")
                return True, audio_content
            else:
                return False, b""
        except Exception as e:
            logger.error(f"Google TTS error: {e}")
            return False, b""
    
    def speak_with_fallback(self, text: str, provider_order: Optional[List[str]] = None, voice: str = "nova") -> Tuple[bool, bytes]:
        """
        Generate speech with automatic fallback.
        Default order: edge → groq → openai → google
        Returns: (success, audio_bytes)
        """
        if not provider_order:
            provider_order = ["edge", "groq", "openai", "google"]
        
        for provider in provider_order:
            if provider == "edge":
                success, audio = self.speak_with_edge(text, voice=voice)
            elif provider == "groq":
                success, audio = self.speak_with_groq(text, voice=voice)
            elif provider == "openai":
                success, audio = self.speak_with_openai(text, voice=voice)
            elif provider == "google":
                success, audio = self.speak_with_google(text)
            else:
                continue
            
            if success:
                logger.info(f"TTS successful with {provider}")
                return True, audio
        
        logger.warning("All TTS providers failed")
        return False, b""
    
    def get_available_providers(self) -> List[str]:
        """Return list of available TTS providers."""
        providers = []
        if EDGE_TTS_AVAILABLE:
            providers.append("edge")
        if self.groq_api_key:
            providers.append("groq")
        if self.openai_api_key:
            providers.append("openai")
        if self.google_api_key:
            providers.append("google")
        return providers


# Global instances
_voice_transcriber: Optional[VoiceTranscriber] = None
_text_to_speech: Optional[TextToSpeech] = None


def initialize_voice_system() -> Tuple[VoiceTranscriber, TextToSpeech]:
    """Initialize voice transcription and TTS."""
    global _voice_transcriber, _text_to_speech
    _voice_transcriber = VoiceTranscriber()
    _text_to_speech = TextToSpeech()
    logger.info("Voice transcription and TTS initialized")
    return _voice_transcriber, _text_to_speech


def get_voice_transcriber() -> Optional[VoiceTranscriber]:
    """Get global transcriber instance."""
    if _voice_transcriber is None:
        initialize_voice_system()
    return _voice_transcriber


def get_text_to_speech() -> Optional[TextToSpeech]:
    """Get global TTS instance."""
    if _text_to_speech is None:
        initialize_voice_system()
    return _text_to_speech


if __name__ == "__main__":
    # Quick demo
    logging.basicConfig(level=logging.INFO)
    
    transcriber = VoiceTranscriber()
    print("Voice Transcriber Stats:", json.dumps(transcriber.get_stats(), indent=2))
    
    tts = TextToSpeech()
    print("\nTTS available:", tts.speak_with_fallback("Ciao, questo è un test"))
