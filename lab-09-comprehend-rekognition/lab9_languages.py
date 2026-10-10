"""CITS5503 Lab 9 - turn a Comprehend language code into an English name.

detect_dominant_language returns an RFC 5646 code ('en', 'fr', 'zh-TW'), but the
lab asks for the name of the language in English. There is nothing in the
standard library that does this, so:

  1. if Babel or pycountry happens to be installed in the venv, use it - those
     ship the full CLDR/ISO 639 tables and stay current;
  2. otherwise fall back to the table below, which covers every language
     Comprehend's DetectDominantLanguage documents.

Neither library is required. The table alone answers all four lab texts.
"""

# The languages DetectDominantLanguage can return, keyed by the code it returns.
# Regional variants that Comprehend reports separately are listed separately,
# because collapsing 'zh-TW' to 'Chinese' would lose what the model actually
# decided.
LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "as": "Assamese",
    "az": "Azerbaijani",
    "ba": "Bashkir",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bo": "Tibetan",
    "br": "Breton",
    "bs": "Bosnian",
    "ca": "Catalan",
    "ceb": "Cebuano",
    "cs": "Czech",
    "cv": "Chuvash",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fo": "Faroese",
    "fr": "French",
    "fy": "Western Frisian",
    "ga": "Irish",
    "gd": "Scottish Gaelic",
    "gl": "Galician",
    "gn": "Guarani",
    "gu": "Gujarati",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "ht": "Haitian Creole",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "jv": "Javanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "la": "Latin",
    "lb": "Luxembourgish",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mg": "Malagasy",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "ne": "Nepali",
    "new": "Newari",
    "nl": "Dutch",
    "no": "Norwegian",
    "oc": "Occitan",
    "or": "Odia",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "qu": "Quechua",
    "ro": "Romanian",
    "ru": "Russian",
    "sa": "Sanskrit",
    "sco": "Scots",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "su": "Sundanese",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "tg": "Tajik",
    "th": "Thai",
    "tk": "Turkmen",
    "tl": "Tagalog",
    "tr": "Turkish",
    "tt": "Tatar",
    "ug": "Uyghur",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "zh": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
}


def _from_library(code: str) -> str:
    """Ask Babel or pycountry, if either is installed. Returns "" if not."""
    try:
        from babel import Locale  # type: ignore

        return Locale.parse(code.replace("-", "_")).get_display_name("en") or ""
    except Exception:
        pass

    try:
        import pycountry  # type: ignore

        base = code.split("-")[0]
        found = pycountry.languages.get(alpha_2=base) or pycountry.languages.get(alpha_3=base)
        if found is not None:
            return getattr(found, "name", "")
    except Exception:
        pass

    return ""


def language_name(code: str) -> str:
    """English name for a Comprehend language code.

    Falls back to the code itself so an unmapped language still prints something
    useful rather than raising in the middle of a demonstration.
    """
    if not code:
        return "Unknown"

    # The built-in table wins for exact matches: it keeps 'zh' and 'zh-TW'
    # distinct, which the library display names do not always do.
    if code in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[code]

    from_lib = _from_library(code)
    if from_lib:
        return from_lib

    base = code.split("-")[0]
    if base in LANGUAGE_NAMES:
        return f"{LANGUAGE_NAMES[base]} ({code})"

    return code


def as_percentage(score: float) -> int:
    """Confidence in [0,1] as a whole percentage.

    Truncated, not rounded. The lab sheet's own example is 0.9961233 printed as
    99%, which rounding would turn into 100% - and claiming 100% confidence for
    a model that did not report 1.0 is the wrong way to be wrong.
    """
    return int(score * 100)
