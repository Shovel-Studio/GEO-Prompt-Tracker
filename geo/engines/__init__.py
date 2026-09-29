"""Maps the template's Platform names to engine implementations."""
from .chatgpt import engine as chatgpt
from .gemini import engine as gemini
from .perplexity import engine as perplexity

ENGINES = {
    "ChatGPT": chatgpt,
    "Perplexity": perplexity,
    "Gemini": gemini,
}
