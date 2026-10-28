"""Pinned OvisOCR3 defaults with compatibility for existing OvisOCR2 deployments."""
import os

MODEL_NAME = 'ATH-MaaS/OvisOCR3'
MODEL_REVISION = '4833d5d2afa6e769ee933f024504d67ace80a24c'
LEGACY_REPOS = {'ATH-MaaS/OvisOCR2', 'AIDC-AI/OvisOCR2'}
LEGACY_REVISION = '65c619d374b55d4152e85150fc1b003700bc1f0c'


def resolve_model_settings(environ=None):
    env = os.environ if environ is None else environ
    explicit = env.get('OVISOCR3_MODEL_NAME')
    name = explicit or env.get('OVISOCR2_MODEL_NAME') or MODEL_NAME
    legacy = name in LEGACY_REPOS
    if legacy:
        name = MODEL_NAME
    revision = env.get('OVISOCR3_MODEL_REVISION') or env.get('OVISOCR2_MODEL_REVISION')
    if name == MODEL_NAME and (legacy or not revision or revision == LEGACY_REVISION):
        revision = env.get('OVISOCR3_MODEL_REVISION') or MODEL_REVISION
    return name, revision


def ovis_env(suffix, default):
    return os.getenv('OVISOCR3_' + suffix, os.getenv('OVISOCR2_' + suffix, default))
