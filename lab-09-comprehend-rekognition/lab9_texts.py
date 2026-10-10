"""CITS5503 Lab 9 - the four test texts from the lab sheet.

Every Comprehend section of the lab is tested against the same four texts, so
they live here once and each analysis reads them from here. Copying them into
five scripts would mean five chances for a typo to change what the model sees.

Comprehend's synchronous APIs accept at most 5,000 UTF-8 bytes per call, which
is checked here rather than at each call site.
"""

MAX_BYTES = 5000  # Comprehend's documented limit for the synchronous Detect* APIs

# Keys are the labels printed in the output; they are not language codes, since
# working out the language is one of the things the lab asks the model to do.
TEXTS = {
    "English": (
        "The French Revolution was a period of social and political upheaval "
        "in France and its colonies beginning in 1789 and ending in 1799."
    ),
    "Spanish": (
        "El Quijote es la obra m\u00e1s conocida de Miguel de Cervantes Saavedra. "
        "Publicada su primera parte con el t\u00edtulo de El ingenioso hidalgo don "
        "Quijote de la Mancha a comienzos de 1605, es una de las obras m\u00e1s "
        "destacadas de la literatura espa\u00f1ola y la literatura universal, y una "
        "de las m\u00e1s traducidas. En 1615 aparecer\u00eda la segunda parte del Quijote "
        "de Cervantes con el t\u00edtulo de El ingenioso caballero don Quijote de la "
        "Mancha."
    ),
    "French": (
        "Moi je n'\u00e9tais rien Et voil\u00e0 qu'aujourd'hui Je suis le gardien Du "
        "sommeil de ses nuits Je l'aime \u00e0 mourir Vous pouvez d\u00e9truire Tout ce "
        "qu'il vous plaira Elle n'a qu'\u00e0 ouvrir L'espace de ses bras Pour tout "
        "reconstruire Pour tout reconstruire Je l'aime \u00e0 mourir"
    ),
    "Italian": "L'amor che move il sole e l'altre stelle.",
}

# What language each text actually is. The analyses never read this - they ask
# Comprehend - but lab9_verify.sh checks the model's answer against it, which is
# what makes section [2] a test rather than a demonstration.
EXPECTED_LANGUAGES = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "Italian": "it",
}

# Where each text came from, printed alongside the results so the source of a
# surprising sentiment score is obvious.
SOURCES = {
    "English": "Wikipedia summary of the French Revolution",
    "Spanish": "Wikipedia summary of Don Quijote",
    "French": 'Song lyric: "Je l\'aime a mourir" - Francis Cabrel',
    "Italian": 'Closing line of Dante\'s "Divine Comedy"',
}


def check_length(text: str) -> None:
    """Raise before the API call rather than after a TextSizeLimitExceeded."""
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(
            f"Text is {size} UTF-8 bytes; Comprehend's synchronous APIs accept "
            f"at most {MAX_BYTES}. Split it, or use the batch/async APIs."
        )


def selected(names=None) -> dict:
    """The requested subset of the texts, in lab sheet order.

    names may be labels ('English') or a case-insensitive prefix ('eng').
    """
    if not names:
        return dict(TEXTS)

    chosen = {}
    for wanted in names:
        for label in TEXTS:
            if label.lower().startswith(wanted.lower()):
                chosen[label] = TEXTS[label]
                break
        else:
            raise KeyError(
                f"No text called '{wanted}'. Available: {', '.join(TEXTS)}"
            )
    return chosen
