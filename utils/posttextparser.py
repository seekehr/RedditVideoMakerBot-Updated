import os
import re
import time
from typing import List, Dict, Any

import spacy

from utils.console import print_step
from utils.voice import sanitize_text
from utils import settings # Added for accessing config


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
        sanitized_audio_text = sanitize_text(audio_text_candidate)

        if not sanitized_audio_text:
            continue

        current_sentence_visual_chunks: List[str] = []
        if max_words_per_visual_chunk > 0:
            words_in_sentence = sanitized_audio_text.split()
            if not words_in_sentence:
                continue
            
            current_visual_chunk_words = []
            for word in words_in_sentence:
                current_visual_chunk_words.append(word)
                if len(current_visual_chunk_words) >= max_words_per_visual_chunk:
                    current_sentence_visual_chunks.append(" ".join(current_visual_chunk_words))
                    current_visual_chunk_words = []
            if current_visual_chunk_words:
                current_sentence_visual_chunks.append(" ".join(current_visual_chunk_words))
        else:
            current_sentence_visual_chunks.append(sanitized_audio_text)

        if current_sentence_visual_chunks:
            parsed_content.append({
                "audio_text": sanitized_audio_text,
                "visual_chunks": current_sentence_visual_chunks
            })

    return parsed_content
