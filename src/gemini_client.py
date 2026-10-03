import os
import json
import io
import re
from typing import Dict, Any, Optional
from PIL import Image

from src.config import Config
from src.persona import (
    SRI_LANKAN_CADDY_SYSTEM_PROMPT,
    ROUND_SUMMARY_PROMPT_TEMPLATE,
    TEE_TIME_OCR_PROMPT
)

class GeminiService:
    def __init__(self):
        self.api_key = Config.GEMINI_API_KEY
        self.model_name = Config.GEMINI_MODEL
        # Ordered fallback chain: primary model first, then models with separate free-tier quotas
        self.model_chain = [self.model_name] + [
            m for m in Config.GEMINI_FALLBACK_MODELS if m != self.model_name
        ]
        self._init_client()

    def _generate(self, contents, system_instruction: Optional[str] = None) -> str:
        """
        Calls Gemini, automatically falling back to the next model in the chain
        on quota exhaustion (429), overload (503) or unavailable model (404).
        """
        if self.sdk_type != "google-genai":
            return self.client.generate_content(contents).text.strip()

        config = {"system_instruction": system_instruction} if system_instruction else None
        last_err: Optional[Exception] = None
        for model in self.model_chain:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
                if model != self.model_name:
                    print(f"[Gemini] Served by fallback model: {model}")
                return self._trim((response.text or "").strip())
            except Exception as e:
                err = str(e)
                if any(code in err for code in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "404", "NOT_FOUND")):
                    print(f"[Gemini] Model {model} unavailable ({err[:60]}...). Trying next model.")
                    last_err = e
                    continue
                raise
        raise last_err or RuntimeError("All Gemini models failed")

    @staticmethod
    def _trim(text: str, max_chars: int = 320) -> str:
        """Safety net: if the model ignores the length rules, keep only the first 2 sentences."""
        if len(text) <= max_chars or text.lstrip().startswith(("{", "[", "```")):
            return text  # leave short replies and JSON (OCR) untouched
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return " ".join(sentences[:2]).strip()

    def _init_client(self):
        if not self.api_key:
            print("[Gemini] WARNING: GEMINI_API_KEY is not set. AI features will run in mock/fallback mode.")
            self.client = None
            return

        try:
            # Try new unified google-genai SDK first
            from google import genai
            self.client = genai.Client(api_key=self.api_key)
            self.sdk_type = "google-genai"
            print(f"[Gemini] Initialized using google-genai SDK with model: {self.model_name}")
        except ImportError:
            try:
                # Fallback to google-generativeai SDK
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self.client = genai.GenerativeModel(
                    model_name=self.model_name,
                    system_instruction=SRI_LANKAN_CADDY_SYSTEM_PROMPT
                )
                self.sdk_type = "google-generativeai"
                print(f"[Gemini] Initialized using google-generativeai SDK with model: {self.model_name}")
            except Exception as e:
                print(f"[Gemini] Error initializing Gemini SDK: {e}")
                self.client = None

    def analyze_tee_time_image(self, image_bytes: bytes) -> Optional[Dict[str, Any]]:
        """
        Multimodal analysis of a golf booking screenshot using Gemini.
        Returns parsed JSON or None if not a booking.
        """
        if not self.client:
            print("[Gemini] No API key configured for tee time image analysis.")
            return None

        try:
            pil_image = Image.open(io.BytesIO(image_bytes))
            
            text = self._generate([TEE_TIME_OCR_PROMPT, pil_image])

            # Clean json response
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            data = json.loads(text)
            if data.get("is_tee_time_booking"):
                return data
            return None
        except Exception as e:
            print(f"[Gemini] Error analyzing booking image: {e}")
            return None

    def chat_as_caddy(self, user_message: str, context: Optional[str] = None) -> str:
        """
        Responds to chat queries as Caddy, the Sri Lankan golf caddy.
        """
        if not self.client:
            return "Aiyo machan! My AI brain (GEMINI_API_KEY) is missing from .env! How am I supposed to roast your slices without my script?! 😂"

        prompt = f"User asks: {user_message}"
        if context:
            prompt = f"Context about upcoming golf / group:\n{context}\n\n{prompt}"

        try:
            return self._generate(prompt, SRI_LANKAN_CADDY_SYSTEM_PROMPT)
        except Exception as e:
            print(f"[Gemini] Chat error: {e}")
            return "Sorry machan, my brain's offline for a bit. Try again shortly."

    def generate_round_summary(self, round_stats: Dict[str, Any]) -> str:
        """
        Generates a short, genuine post-round summary in Caddy's voice.
        """
        if not self.client:
            return f"⛳ Round complete for {round_stats.get('player_name')}! Score: {round_stats.get('total_score')}."

        prompt = ROUND_SUMMARY_PROMPT_TEMPLATE.format(
            player_name=round_stats.get("player_name", "Golfer"),
            course_name=round_stats.get("course_name", "Unknown Course"),
            round_date=round_stats.get("round_date", "Today"),
            total_score=round_stats.get("total_score", "N/A"),
            score_to_par=round_stats.get("score_to_par", "E"),
            front_9=round_stats.get("front_9", "N/A"),
            back_9=round_stats.get("back_9", "N/A"),
            fairways_hit=round_stats.get("fairways_hit", 0),
            fairways_total=round_stats.get("fairways_total", 14),
            fairway_pct=round_stats.get("fairway_pct", 0),
            gir_hit=round_stats.get("gir_hit", 0),
            gir_total=round_stats.get("gir_total", 18),
            gir_pct=round_stats.get("gir_pct", 0),
            total_putts=round_stats.get("total_putts", 36),
            putts_per_hole=round_stats.get("putts_per_hole", 2.0),
            best_hole=round_stats.get("best_hole", "N/A"),
            worst_hole=round_stats.get("worst_hole", "N/A"),
            penalties=round_stats.get("penalties", 0)
        )

        try:
            return self._generate(prompt, SRI_LANKAN_CADDY_SYSTEM_PROMPT)
        except Exception as e:
            print(f"[Gemini] Error generating round summary: {e}")
            return f"{round_stats.get('player_name')} shot {round_stats.get('total_score')} ({round_stats.get('score_to_par')}) at {round_stats.get('course_name')}."
