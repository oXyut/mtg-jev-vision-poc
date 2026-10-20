"""Frozen wording-only camera experiment, separate from production defaults."""
from .camera_prompts import pair

BASE_COMMIT = '1b0261e896e76ab8d928fd5133ce3c02f0f50dd2'
CONDITIONS = ('mtg_plain', 'mtg_explicit', 'generic_plain', 'generic_explicit')
ELIGIBILITY = ('Treat a card as eligible even if it overlaps another card, is rotated, '
               'or is partly occluded by an object placed on it.')
BASE = {
    'search_present': 'Is any Magic: The Gathering card printing visible outside the gray masked areas in photo?',
    'search_box': 'The visible printed area of the leftmost Magic: The Gathering card outside the gray masked areas in photo.',
    'crop_present': 'Is a Magic: The Gathering card visibly present in this crop?',
    'crop_single': 'Does this crop primarily show one identifiable Magic: The Gathering card? Ignore tiny neighboring edge fragments.',
    'crop_box': 'The visible printed rectangle of the Magic: The Gathering card nearest the center of this crop, excluding sleeve and hidden parts.',
}


def instructions(condition):
    if condition not in CONDITIONS:
        raise ValueError('Unknown factorial condition')
    generic, explicit = condition.startswith('generic_'), condition.endswith('_explicit')
    return {key: (value.replace('Magic: The Gathering card', 'trading card') if generic else value)
            + (' '+ELIGIBILITY if explicit else '') for key, value in BASE.items()}


def factories(condition):
    text = instructions(condition)

    def search(unused, reference):
        return pair(text['search_present'], text['search_box'], reference)

    def crop(unused, reference):
        document = pair(text['crop_present'], text['crop_box'], reference)
        document['questions']['single'] = {'type': 'noul', 'instructions': text['crop_single']}
        return document

    return search, crop


def schedule(repeats=4):
    """Four rotated orders per photo; photo first alternates. No random draw."""
    if type(repeats) is not int or not 1 <= repeats <= 4:
        raise ValueError('Repeats must be 1..4')
    runs = []
    for repeat in range(repeats):
        scenes = ('scene-A', 'scene-B') if repeat % 2 == 0 else ('scene-B', 'scene-A')
        for scene in scenes:
            offset = (repeat+(0 if scene == 'scene-A' else 2)) % 4
            order = CONDITIONS[offset:]+CONDITIONS[:offset]
            runs.extend({'repeat': repeat+1, 'scene': scene, 'condition': condition} for condition in order)
    return runs
