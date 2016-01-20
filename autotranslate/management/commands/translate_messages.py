from __future__ import unicode_literals

import glob
import logging
import os
import re
from optparse import make_option

import polib
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils._os import npath, upath

from autotranslate.utils import translate_strings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ('autotranslate all the message files that have been generated '
            'using the `makemessages` command.')

    option_list = BaseCommand.option_list + (
        make_option('--locale', '-l', default=[], dest='locale', action='append',
                    help='autotranslate the message files for the given locale(s) (e.g. pt_BR). '
                         'can be used multiple times.'),
        make_option('--exclude', '-x', dest='exclude', action='append', default=[],
                    help='Locales to exclude. Default is none. Can be used multiple times.'),
        make_option('--untranslated', '-u', default=False, dest='skip_translated', action='store_true',
                    help='autotranslate the fuzzy and empty messages only.'),
        make_option('--set-fuzzy', '-f', dest='fuzzy', action='store_true', default=False,
                    help='Set the fuzzy flag on autotranslated messages.'),
    )

    def add_arguments(self, parser):
        # Previously, only the standard optparse library was supported and
        # you would have to extend the command option_list variable with optparse.make_option().
        # See: https://docs.djangoproject.com/en/1.8/howto/custom-management-commands/#accepting-optional-arguments
        # In django 1.8, these custom options can be added in the add_arguments()
        parser.add_argument('--locale', '-l', default=[], dest='locale', action='append',
                            help='autotranslate the message files for the given locale(s) (e.g. pt_BR). '
                                 'can be used multiple times.')
        parser.add_argument('--exclude', '-x', dest='exclude', action='append', default=[],
            help='Locales to exclude. Default is none. Can be used multiple times.')
        parser.add_argument('--untranslated', '-u', default=False, dest='skip_translated', action='store_true',
                            help='autotranslate the fuzzy and empty messages only.')
        parser.add_argument('--set-fuzzy', '-f', dest='fuzzy', action='store_true', default=False,
                            help='Set the fuzzy flag on autotranslated messages.')

    def set_options(self, **options):
        self.locale = options['locale']
        self.exclude = options['exclude']
        self.skip_translated = options['skip_translated']
        self.set_fuzzy = options['fuzzy']

    def handle(self, *args, **options):
        self.set_options(**options)

        result = self.find_po_files(locale=self.locale, exclude=self.exclude)
        for (dirname, filename) in result:
            # get the target language from the parent folder name
            target_language = os.path.basename(os.path.dirname(dirname))
            fullname = os.path.join(dirname, filename)
            self.translate_file(fullname, target_language)

    def translate_file(self, filepath, target_language):
        """
        convenience method for translating a pot file

        :param filepath:        path of the file to be translated (it should be a pot file)
        :param target_language: language in which the file needs to be translated
        """
        logger.info('filling up translations for locale `{}`'.format(target_language))

        po = polib.pofile(filepath)
        strings = self.get_strings_to_translate(po)
        # translate the strings,
        # all the translated strings are returned
        # in the same order on the same index
        # viz. [a, b] -> [trans_a, trans_b]
        translated_strings = translate_strings(strings, target_language, 'en', False)
        self.update_translations(po, translated_strings)
        po.save()

    def need_translate(self, entry):
        return not self.skip_translated or not entry.translated() or not entry.obsolete

    def get_strings_to_translate(self, po):
        """Return list of string to translate from po file.

        :param po: POFile object to translate
        :type po: polib.POFile
        :return: list of string to translate
        :rtype: collections.Iterable[six.text_type]
        """
        strings = []
        for index, entry in enumerate(po):
            if not self.need_translate(entry):
                continue
            strings.append(humanize_placeholders(entry.msgid))
            if entry.msgid_plural:
                strings.append(humanize_placeholders(entry.msgid_plural))
        return strings

    def update_translations(self, entries, translated_strings):
        """Update translations in entries.

        The order and number of translations should match to get_strings_to_translate() result.

        :param entries: list of entries to translate
        :type entries: collections.Iterable[polib.POEntry] | polib.POFile
        :param translated_strings: list of translations
        :type translated_strings: collections.Iterable[six.text_type]
        """
        translations = iter(translated_strings)
        for entry in entries:
            if not self.need_translate(entry):
                continue

            if entry.msgid_plural:
                # fill the first plural form with the entry.msgid translation
                translation = next(translations)
                translation = fix_translation(entry.msgid, translation)
                entry.msgstr_plural[0] = translation

                # fill the rest of plural forms with the entry.msgid_plural translation
                translation = next(translations)
                translation = fix_translation(entry.msgid_plural, translation)
                for k, v in entry.msgstr_plural.items():
                    if k != 0:
                        entry.msgstr_plural[k] = translation
            else:
                translation = next(translations)
                translation = fix_translation(entry.msgid, translation)
                entry.msgstr = translation

            # Set the 'fuzzy' flag on translation
            if self.set_fuzzy and 'fuzzy' not in entry.flags:
                entry.flags.append('fuzzy')

    def find_po_files(self, locale, exclude):
        """Looks for `po` files.

        This code was mostly copypasted from Django `compilemessages` management
        command to be compatible with its search algorithm. See
        https://github.com/django/django/blob/5d35b53/django/core/management/commands/compilemessages.py#L58-L95
        """
        basedirs = [os.path.join('conf', 'locale'), 'locale']
        if os.environ.get('DJANGO_SETTINGS_MODULE'):
            from django.conf import settings
            basedirs.extend(upath(path) for path in settings.LOCALE_PATHS)

        # Walk entire tree, looking for locale directories
        for dirpath, dirnames, filenames in os.walk('.', topdown=True):
            for dirname in dirnames:
                if dirname == 'locale':
                    basedirs.append(os.path.join(dirpath, dirname))

        # Gather existing directories.
        basedirs = set(map(os.path.abspath, filter(os.path.isdir, basedirs)))

        if not basedirs:
            raise CommandError("This script should be run from the Django Git "
                               "checkout or your project or app tree, or with "
                               "the settings module specified.")

        # Build locale list
        all_locales = []
        for basedir in basedirs:
            locale_dirs = filter(os.path.isdir, glob.glob('%s/*' % basedir))
            all_locales.extend(map(os.path.basename, locale_dirs))

        # Account for excluded locales
        locales = locale or all_locales
        locales = set(locales) - set(exclude)

        for basedir in basedirs:
            if locales:
                dirs = [os.path.join(basedir, l, 'LC_MESSAGES') for l in locales]
            else:
                dirs = [basedir]
            locations = []
            for ldir in dirs:
                for dirpath, dirnames, filenames in os.walk(ldir):
                    for filename in filenames:
                        if filename.endswith('.po'):
                            yield (dirpath, filename)


def humanize_placeholders(msgid):
    """Convert placeholders to the (google translate) service friendly form.

    %(name)s -> __name__
    %s       -> __item__
    %d       -> __number__
    """
    return re.sub(
            r'%(?:\((\w+)\))?([sd])',
            lambda match: r'__{0}__'.format(
                    match.group(1).lower() if match.group(1) else 'number' if match.group(2) == 'd' else 'item'),
            msgid)


def restore_placeholders(msgid, translation):
    """Restore placeholders in the translated message."""
    placehoders = re.findall(r'(\s*)(%(?:\(\w+\))?[sd])(\s*)', msgid)
    return re.sub(
            r'(\s*)(__[\w]+?__)(\s*)',
            lambda matches: '{0}{1}{2}'.format(placehoders[0][0], placehoders[0][1], placehoders.pop(0)[2]),
            translation)


def fix_translation(msgid, translation):
    # Google Translate removes a lot of formatting, these are the fixes:
    # - Add newline in the beginning if msgid also has that
    if msgid.startswith('\n') and not translation.startswith('\n'):
        translation = u'\n' + translation

    # - Add newline at the end if msgid also has that
    if msgid.endswith('\n') and not translation.endswith('\n'):
        translation += u'\n'

    # Remove spaces that have been placed between %(id) tags
    translation = restore_placeholders(msgid, translation)
    return translation
