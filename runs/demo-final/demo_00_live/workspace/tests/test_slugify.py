import unittest
from slugify import slugify


class TestSlugify(unittest.TestCase):
    def test_unicode_input_preserves_letters_and_strips_punctuation(self):
        # Unicode letters (é, ü, 中) preserved, Unicode punctuation (，！) stripped
        self.assertEqual(slugify('é ü, 中！'), 'é-ü-中')

    def test_ascii_punctuation_stripped_and_words_joined_with_hyphens(self):
        self.assertEqual(slugify('Hello,   World!  '), 'hello-world')
        self.assertEqual(slugify('foo-bar.baz'), 'foo-bar-baz')

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(slugify(''), '')

    def test_multiple_whitespace_collapsed_into_single_hyphen(self):
        self.assertEqual(slugify('a\tb\tc'), 'a-b-c')
        self.assertEqual(slugify('  Hello   World  '), 'hello-world')
        self.assertEqual(slugify('one\n\ntwo\nthree'), 'one-two-three')

    def test_mixed_case_input_lowercased(self):
        self.assertEqual(slugify('MiXeD CaSe'), 'mixed-case')
        self.assertEqual(slugify('ÜNIÇÖDE'), 'üniçöde')


if __name__ == '__main__':
    unittest.main()