import unittest

from track_service import normalize_subtitle_lines


class SubtitleTimelineTests(unittest.TestCase):
    def test_lines_are_sorted_and_non_finite_times_are_rejected(self):
        lines = normalize_subtitle_lines(
            {
                "body": [
                    {"from": 8.0, "to": 10.0, "content": "later"},
                    {"from": "nan", "to": 3.0, "content": "invalid"},
                    {"from": 1.0, "to": 2.0, "content": "first"},
                ]
            }
        )

        self.assertEqual(
            lines,
            [
                {"from": 1.0, "to": 2.0, "text": "first"},
                {"from": 8.0, "to": 10.0, "text": "later"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
