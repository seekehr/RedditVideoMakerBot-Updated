import os
import re
import time
from typing import List, Dict, Any

import spacy

from utils.console import print_step
from utils.voice import sanitize_text
from utils import settings

TIKTOK_TTS_CHAR_LIMIT = 250

# working good
def posttextparser(obj, *, tried: bool = False) -> List[Dict[str, Any]]:
    raw_text: str = re.sub("\n", " ", obj)
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError as e:
        if not tried:
            os.system("python -m spacy download en_core_web_sm")
            time.sleep(5)
            return posttextparser(obj, tried=True)
        print_step(
            "The spacy model can't load. You need to install it with the command \npython -m spacy download en_core_web_sm "
        )
        raise e

    doc = nlp(raw_text)
    
    parsed_content: List[Dict[str, Any]] = []
    max_words_per_visual_chunk = settings.config["settings"].get("max_words_per_segment", 0)

    for sent in doc.sents:
        audio_text_candidate = sent.text.strip()
        sanitized_full_sentence_text = sanitize_text(audio_text_candidate)

        if not sanitized_full_sentence_text:
            continue

        sentence_audio_chunks_to_process: List[str] = []
        if len(sanitized_full_sentence_text) > TIKTOK_TTS_CHAR_LIMIT:
            words_of_sentence = sanitized_full_sentence_text.split()
            current_chunk_words = []
            current_chunk_len = 0
            for word in words_of_sentence:
                if current_chunk_len + len(word) + (1 if current_chunk_words else 0) > TIKTOK_TTS_CHAR_LIMIT:
                    if current_chunk_words:
                        sentence_audio_chunks_to_process.append(" ".join(current_chunk_words))
                    current_chunk_words = [word]
                    current_chunk_len = len(word)
                else:
                    current_chunk_words.append(word)
                    current_chunk_len += len(word) + (1 if len(current_chunk_words) > 1 else 0)
            
            if current_chunk_words:
                sentence_audio_chunks_to_process.append(" ".join(current_chunk_words))
            
            if not sentence_audio_chunks_to_process and sanitized_full_sentence_text:
                 sentence_audio_chunks_to_process.append(sanitized_full_sentence_text[:TIKTOK_TTS_CHAR_LIMIT])


        else:
            sentence_audio_chunks_to_process.append(sanitized_full_sentence_text)

        for audio_chunk_text in sentence_audio_chunks_to_process:
            if not audio_chunk_text:
                continue

            current_sentence_visual_chunks: List[str] = []
            if max_words_per_visual_chunk > 0:
                words_in_audio_chunk = audio_chunk_text.split()
                if not words_in_audio_chunk:
                    continue
                
                current_visual_chunk_words = []
                for word in words_in_audio_chunk:
                    current_visual_chunk_words.append(word)
                    if len(current_visual_chunk_words) >= max_words_per_visual_chunk:
                        current_sentence_visual_chunks.append(" ".join(current_visual_chunk_words))
                        current_visual_chunk_words = []
                if current_visual_chunk_words:
                    current_sentence_visual_chunks.append(" ".join(current_visual_chunk_words))
            else:
                current_sentence_visual_chunks.append(audio_chunk_text)

            if current_sentence_visual_chunks:
                parsed_content.append({
                    "audio_text": audio_chunk_text, 
                    "visual_chunks": current_sentence_visual_chunks
                })

    return parsed_content
