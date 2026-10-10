#!/usr/bin/env python3
"""CITS5503 Lab 9 - the AWS Comprehend half of the lab.

One module, one subcommand per section of the lab sheet:

    language     Detect Languages from text     [1] and [2]   Checkpoint 1
    sentiment    Analyse sentiment                             Checkpoint 2
    entities     Detect entities                               Checkpoint 3
    keyphrases   Detect keyphrases                             Checkpoint 4
    syntax       Detect syntaxes                               (no checkpoint)
    all          run all five, in that order

By default the script PROMPTS for a text, analyses whatever is pasted in, and
exits. That is the shape Checkpoints 1-4 ask for: the facilitator picks one of
the four texts at random and you paste it at the prompt.

Run with the shared venv active:
    labenv
    python3 lab9_comprehend.py language     prompt, analyse, exit

Practice and self-testing, which do not prompt:

    python3 lab9_comprehend.py language --all-texts     all four lab texts
    python3 lab9_comprehend.py sentiment --text French   one of them
    python3 lab9_comprehend.py entities --input "..."    your own string
    python3 lab9_comprehend.py syntax --file notes.txt   a file's contents
    python3 lab9_comprehend.py all --json                raw API responses

The language code passed to sentiment/entities/keyphrases/syntax is not
hard-coded: the text is run through detect_dominant_language first and the
winning code is what gets sent, so a pasted Italian text is analysed as Italian
without anyone having to say so. --language overrides that.
"""

import argparse
import json
import sys

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab9_env import load_env, require, report
from lab9_languages import language_name, as_percentage
from lab9_texts import TEXTS, SOURCES, check_length, selected

# Fixed by the Comprehend service model, not by configuration. Checked in
# advance so an unsupported language produces a clear message instead of an
# UnsupportedLanguageException from the middle of a loop.
SENTIMENT_LANGUAGES = {"en", "es", "fr", "de", "it", "pt",
                       "ar", "hi", "ja", "ko", "zh", "zh-TW"}
SYNTAX_LANGUAGES = {"en", "es", "fr", "de", "it", "pt"}

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")

# LAB9_MIN_CONFIDENCE is deliberately not read here. It is a Rekognition
# threshold: the Comprehend Detect* APIs return every result with its score and
# have no confidence filter to set.

comprehend = boto3.client("comprehend", region_name=AWS_REGION)


# --------------------------------------------------------------- formatting
def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f" {title}")
    print("=" * 72)


def text_header(label: str, text: str) -> None:
    print()
    print(f"--- {label} " + "-" * max(0, 68 - len(label)))
    print(f"    source: {SOURCES.get(label, 'entered at the prompt')}")
    preview = text if len(text) <= 100 else text[:97] + "..."
    print(f"    text:   {preview}")
    print()


def dump(response: dict) -> None:
    """Raw API response, with datetimes stringified."""
    response = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(response, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------ the input prompt
def prompt_for_text(multiline: bool = False) -> str:
    """Ask for a text and return it. This is the Checkpoint 1-4 entry point.

    Single line by default: the four lab texts each occupy one line in the lab
    sheet, so pasting one delivers one line no matter how the terminal wraps it,
    and the facilitator presses Enter once. --multiline is there for a text that
    genuinely contains newlines, and ends on a blank line.

    An empty entry re-prompts rather than analysing nothing. Ctrl-C and Ctrl-D
    exit cleanly rather than showing a traceback.
    """
    if multiline:
        print("Enter the text, then press Enter on a blank line to finish:")
    else:
        print("Enter the text to analyse, then press Enter:")

    while True:
        try:
            if multiline:
                lines = []
                while True:
                    line = input()
                    if not line.strip():
                        break
                    lines.append(line)
                text = " ".join(lines).strip()
            else:
                text = input("> ").strip()
        except EOFError:
            sys.exit("\nNo text entered.")
        except KeyboardInterrupt:
            sys.exit("\nCancelled.")

        if text:
            return text
        print("Nothing entered. Paste one of the four texts and press Enter.")


# ------------------------------------------------ [1] and [2] Detect language
def detect_language(text: str) -> dict:
    """The lab sheet's starting point, with the English name and a percentage.

    Comprehend can return more than one candidate language for mixed text. The
    first entry is the dominant one, which is what the message reports; the rest
    are printed underneath so a close second is visible rather than hidden.
    """
    check_length(text)
    response = comprehend.detect_dominant_language(Text=text)
    return response


def show_language(text: str, raw: bool = False) -> str:
    """Print the lab sheet's message and return the dominant language code."""
    response = detect_language(text)
    languages = response["Languages"]

    if raw:
        dump(response)
        return languages[0]["LanguageCode"] if languages else ""

    if not languages:
        print("    No language detected.")
        return ""

    dominant = languages[0]
    name = language_name(dominant["LanguageCode"])
    percent = as_percentage(dominant["Score"])
    print(f"    {name} was detected with {percent}% confidence")

    if len(languages) > 1:
        # One decimal here, unlike the whole-percent message above: a runner-up
        # scoring 0.002 would otherwise print as a flat 0%.
        others = ", ".join(
            f"{language_name(l['LanguageCode'])} {l['Score'] * 100:.1f}%"
            for l in languages[1:]
        )
        print(f"    (other candidates: {others})")

    return dominant["LanguageCode"]


# ----------------------------------------------------------------- sentiment
def analyse_sentiment(text: str, language_code: str) -> dict:
    check_length(text)
    return comprehend.detect_sentiment(Text=text, LanguageCode=language_code)


def show_sentiment(text: str, code: str, raw: bool = False) -> None:
    if code not in SENTIMENT_LANGUAGES:
        print(f"    detect_sentiment does not support '{code}'. "
              f"Supported: {', '.join(sorted(SENTIMENT_LANGUAGES))}")
        return

    response = analyse_sentiment(text, code)
    if raw:
        dump(response)
        return

    scores = response["SentimentScore"]

    # Checkpoint 2 asks for the category and a score dictionary covering all
    # four categories, so the dictionary is printed as a dictionary. The
    # breakdown underneath is for reading, not a substitute for it.
    print(f"    Sentiment:       {response['Sentiment']}")
    print(f"    Sentiment score: {scores}")
    print()
    for key in ("Positive", "Negative", "Neutral", "Mixed"):
        value = scores[key]
        bar = "#" * int(value * 40)
        print(f"      {key:<9} {value * 100:6.2f}%  {bar}")


# ------------------------------------------------------------------ entities
def detect_entities(text: str, language_code: str) -> dict:
    check_length(text)
    return comprehend.detect_entities(Text=text, LanguageCode=language_code)


def show_entities(text: str, code: str, raw: bool = False) -> None:
    if code not in SENTIMENT_LANGUAGES:
        print(f"    detect_entities does not support '{code}'.")
        return

    response = detect_entities(text, code)
    if raw:
        dump(response)
        return

    entities = response["Entities"]
    if not entities:
        print("    No entities detected.")
        return

    print(f"    {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} "
          f"(offsets are into the original string):")
    print(f"      {'TYPE':<14}{'CONF':>7}  {'OFFSET':>11}  TEXT")
    for e in entities:
        span = f"{e['BeginOffset']}-{e['EndOffset']}"
        print(f"      {e['Type']:<14}{e['Score'] * 100:6.1f}%  {span:>11}  {e['Text']}")

    kinds = {}
    for e in entities:
        kinds[e["Type"]] = kinds.get(e["Type"], 0) + 1
    print("      by type: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))


# ---------------------------------------------------------------- keyphrases
def detect_key_phrases(text: str, language_code: str) -> dict:
    check_length(text)
    return comprehend.detect_key_phrases(Text=text, LanguageCode=language_code)


def show_key_phrases(text: str, code: str, raw: bool = False) -> None:
    if code not in SENTIMENT_LANGUAGES:
        print(f"    detect_key_phrases does not support '{code}'.")
        return

    response = detect_key_phrases(text, code)
    if raw:
        dump(response)
        return

    phrases = response["KeyPhrases"]
    if not phrases:
        print("    No key phrases detected.")
        return

    print(f"    {len(phrases)} key phrase(s):")
    print(f"      {'CONF':>7}  {'OFFSET':>11}  PHRASE")
    for p in phrases:
        span = f"{p['BeginOffset']}-{p['EndOffset']}"
        print(f"      {p['Score'] * 100:6.1f}%  {span:>11}  {p['Text']}")


# -------------------------------------------------------------------- syntax
def detect_syntax(text: str, language_code: str) -> dict:
    check_length(text)
    return comprehend.detect_syntax(Text=text, LanguageCode=language_code)


# Part-of-speech tags Comprehend returns, expanded so the output reads without
# the API reference open next to it.
POS_TAGS = {
    "ADJ": "adjective", "ADP": "adposition", "ADV": "adverb", "AUX": "auxiliary",
    "CCONJ": "coordinating conjunction", "CONJ": "coordinating conjunction",
    "DET": "determiner", "INTJ": "interjection", "NOUN": "noun", "NUM": "numeral",
    "O": "other", "PART": "particle", "PRON": "pronoun", "PROPN": "proper noun",
    "PUNCT": "punctuation", "SCONJ": "subordinating conjunction",
    "SYM": "symbol", "VERB": "verb",
}


def show_syntax(text: str, code: str, raw: bool = False, limit: int = 0) -> None:
    if code not in SYNTAX_LANGUAGES:
        print(f"    detect_syntax does not support '{code}'. "
              f"Supported: {', '.join(sorted(SYNTAX_LANGUAGES))}")
        return

    response = detect_syntax(text, code)
    if raw:
        dump(response)
        return

    tokens = response["SyntaxTokens"]
    if not tokens:
        print("    No syntax tokens returned.")
        return

    shown = tokens[:limit] if limit else tokens
    print(f"    {len(tokens)} token(s)"
          f"{f', showing the first {len(shown)}' if len(shown) < len(tokens) else ''}:")
    print(f"      {'ID':>4}  {'TOKEN':<20}{'TAG':<8}{'CONF':>7}  PART OF SPEECH")
    for t in shown:
        pos = t["PartOfSpeech"]
        tag = pos["Tag"]
        print(f"      {t['TokenId']:>4}  {t['Text']:<20}{tag:<8}"
              f"{pos['Score'] * 100:6.1f}%  {POS_TAGS.get(tag, tag.lower())}")

    counts = {}
    for t in tokens:
        tag = t["PartOfSpeech"]["Tag"]
        counts[tag] = counts.get(tag, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    print("      tag counts: " + ", ".join(f"{k} {v}" for k, v in top))


# ----------------------------------------------------------------- the runner
ANALYSES = {
    "language": ("Detect languages from text", None),
    "sentiment": ("Analyse sentiment", show_sentiment),
    "entities": ("Detect entities", show_entities),
    "keyphrases": ("Detect keyphrases", show_key_phrases),
    "syntax": ("Detect syntaxes", show_syntax),
}


def run(which: str, texts: dict, forced_language: str, raw: bool, limit: int) -> int:
    """Run one analysis over the given texts. Returns the number of failures."""
    title, handler = ANALYSES[which]
    banner(f"{title}  ({len(texts)} text(s), region {AWS_REGION})")
    failures = 0

    for label, text in texts.items():
        text_header(label, text)
        try:
            # Every analysis except the language one needs a language code, and
            # the code comes from Comprehend rather than from the label - that
            # is what lets a randomly chosen text be analysed correctly.
            if which == "language":
                show_language(text, raw)
                continue

            code = forced_language or show_language(text)
            if not code:
                print("    Skipped: no dominant language to work from.")
                failures += 1
                continue
            print()

            if which == "syntax":
                handler(text, code, raw, limit)
            else:
                handler(text, code, raw)

        except ClientError as exc:
            error = exc.response["Error"]
            print(f"    [FAIL] {error['Code']}: {error['Message']}")
            failures += 1
            if error["Code"] == "AccessDeniedException":
                print("           Your account may not have comprehend:Detect* permissions.")
            elif error["Code"] == "UnsupportedLanguageException":
                print("           Force a supported language with --language.")
        except ValueError as exc:
            print(f"    [FAIL] {exc}")
            failures += 1

    return failures


def main() -> None:
    ap = argparse.ArgumentParser(
        description="AWS Comprehend analyses for CITS5503 Lab 9.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Sections: " + ", ".join(f"{k} = {v[0]}" for k, v in ANALYSES.items()),
    )
    ap.add_argument("analysis", choices=list(ANALYSES) + ["all"],
                    help="which section of the lab sheet to run")
    ap.add_argument("--all-texts", action="store_true",
                    help="run against all four lab texts instead of prompting")
    ap.add_argument("--text", action="append", metavar="LABEL",
                    help=f"run against these lab texts ({', '.join(TEXTS)}); repeatable")
    ap.add_argument("--input", metavar="STRING",
                    help="analyse this string instead of prompting")
    ap.add_argument("--file", metavar="PATH",
                    help="analyse the contents of this file instead")
    ap.add_argument("--multiline", action="store_true",
                    help="at the prompt, read until a blank line instead of one line")
    ap.add_argument("--language", metavar="CODE",
                    help="force a language code instead of detecting it")
    ap.add_argument("--json", action="store_true",
                    help="print the raw API response instead of a summary")
    ap.add_argument("--syntax-limit", type=int, default=0, metavar="N",
                    help="show only the first N syntax tokens (0 = all)")
    args = ap.parse_args()

    # Input precedence. With none of these given the script prompts, which is
    # what Checkpoints 1-4 ask for; the rest are for practising beforehand.
    sources = [bool(args.input is not None), bool(args.file is not None),
               bool(args.text), bool(args.all_texts)]
    if sum(sources) > 1:
        ap.error("give at most one of --input, --file, --text and --all-texts.")

    prompted = False
    if args.input is not None:
        texts = {"Command line": args.input}
    elif args.file is not None:
        try:
            texts = {args.file: open(args.file, encoding="utf-8").read()}
        except OSError as exc:
            sys.exit(f"Could not read {args.file}: {exc}")
    elif args.text or args.all_texts:
        try:
            texts = selected(args.text)
        except KeyError as exc:
            sys.exit(exc.args[0])  # str(KeyError) would wrap the message in quotes
    else:
        prompted = True
        texts = None  # filled in after the connectivity check, so a broken
                      # setup fails before the facilitator pastes anything

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "Analysis": args.analysis,
        "Texts": "entered at the prompt" if prompted else ", ".join(texts),
        "Language": args.language or "detected from the text",
    })

    try:
        comprehend.detect_dominant_language(Text="connectivity check")
    except NoCredentialsError:
        sys.exit("No AWS credentials. Run 'aws configure' (Lab 1) first.")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("InvalidSignatureException", "SignatureDoesNotMatch"):
            sys.exit("Signature rejected - WSL2's clock has drifted. "
                     "Fix it with:  sudo hwclock -s")
        if code == "AccessDeniedException":
            sys.exit("Access denied on comprehend:DetectDominantLanguage. "
                     "Check the permissions on your lab credentials.")
        # Anything else: still exit with a message rather than a traceback. A
        # stack trace in front of a facilitator is not a normal exit.
        sys.exit(f"Comprehend is not reachable: {code}: "
                 f"{exc.response['Error'].get('Message', '')}")

    # Prompt only once the service is known to be reachable. Asking for the text
    # first and failing afterwards would waste the facilitator's paste.
    if prompted:
        texts = {"Entered text": prompt_for_text(args.multiline)}

    order = list(ANALYSES) if args.analysis == "all" else [args.analysis]
    failures = 0
    for which in order:
        failures += run(which, texts, args.language or "", args.json, args.syntax_limit)

    print()
    if failures:
        print(f"Done, with {failures} failure(s) above.")
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
