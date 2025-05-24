import unittest
import json
from unittest.mock import patch, mock_open
import os
import sys

# Add parent directory to path to import main
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app, send_telegram_alert, analyzer, scan_and_alert, sent_hashes as main_global_sent_hashes # Assuming 'analyzer' is accessible for testing sentiment
# If main.py needs to be loadable as a module without running main() immediately,
# ensure the main execution block is guarded by if __name__ == '__main__': (which it is)

class TestMainApp(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # We will patch main.TELEGRAM_BOT_TOKEN and main.TELEGRAM_CHAT_IDS directly in tests


    @patch('main.requests.post')
    @patch('main.TELEGRAM_CHAT_IDS', ['12345'])
    @patch('main.TELEGRAM_BOT_TOKEN', 'test_token')
    def test_send_telegram_alert_success(self, mock_post):
        mock_post.return_value.raise_for_status = lambda: None # Mock successful post
        send_telegram_alert("Test message")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn('test_token', args[0]) # Check URL
        self.assertEqual(kwargs['data']['text'], "Test message")
        self.assertEqual(kwargs['data']['chat_id'], "12345")

    @patch('main.requests.post')
    @patch('main.TELEGRAM_CHAT_IDS', [])
    @patch('main.TELEGRAM_BOT_TOKEN', None)
    def test_send_telegram_alert_no_config_token_none(self, mock_post):
        send_telegram_alert("Test message no config")
        mock_post.assert_not_called()

    @patch('main.requests.post')
    @patch('main.TELEGRAM_CHAT_IDS', [])
    @patch('main.TELEGRAM_BOT_TOKEN', 'test_token')
    def test_send_telegram_alert_no_config_chat_ids_empty(self, mock_post):
        send_telegram_alert("Test message no config")
        mock_post.assert_not_called()

    @patch('main.requests.post')
    @patch('main.TELEGRAM_CHAT_IDS', ['12345'])
    @patch('main.TELEGRAM_BOT_TOKEN', None)
    def test_send_telegram_alert_no_config_token_none_with_ids(self, mock_post):
        send_telegram_alert("Test message no config")
        mock_post.assert_not_called()


    def test_sentiment_analysis_positive(self):
        # Direct test of vaderSentiment analyzer instance if possible,
        # or a small helper function in main that uses it.
        # For this example, directly using the global 'analyzer' from main.py
        text = "This is a great and wonderful piece of news!"
        sentiment = analyzer.polarity_scores(text)
        self.assertTrue(sentiment['compound'] > 0)

    def test_sentiment_analysis_negative(self):
        text = "This is terrible, awful, and bad news."
        sentiment = analyzer.polarity_scores(text)
        self.assertTrue(sentiment['compound'] < 0)

    def test_health_route(self):
        response = self.app.get('/health')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data['status'], 'healthy')
        self.assertIn('timestamp', data)

    @patch('main.open', new_callable=mock_open, read_data='[{"ticker": "NVDA", "headline": "Good news", "sentiment": 0.8}]')
    def test_get_alerts_success(self, mock_file):
        response = self.app.get('/alerts')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['ticker'], 'NVDA')

    @patch('main.open', new_callable=mock_open)
    def test_get_alerts_file_not_found(self, mock_file):
        mock_file.side_effect = FileNotFoundError
        response = self.app.get('/alerts')
        self.assertEqual(response.status_code, 404) # Based on refactoring
        data = json.loads(response.get_data(as_text=True))
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'No alerts found.')


    @patch('main.open', new_callable=mock_open, read_data='invalid json')
    def test_get_alerts_json_decode_error(self, mock_file):
        # For json.JSONDecodeError, the mock_open needs to raise it when 'load' is called.
        # A more direct way is to patch json.load directly for this specific test.
        with patch('main.json.load', side_effect=json.JSONDecodeError("Error", "doc", 0)):
             response = self.app.get('/alerts')
             self.assertEqual(response.status_code, 500) # Based on refactoring
             data = json.loads(response.get_data(as_text=True))
             self.assertIn('error', data)
             self.assertEqual(data['error'], 'Error reading alerts data.')

    @patch('main.send_telegram_alert')
    @patch('main.get_price_polygon')
    @patch('main.get_option_data_polygon')
    @patch('main.fetch_marketaux_news')
    @patch('main.analyzer.polarity_scores') # New patch
    @patch('main.open', new_callable=mock_open)
    @patch('main.json.dump') # To verify what's being written
    def test_scan_and_alert_integration(self, mock_json_dump, mock_file_open, mock_polarity_scores, mock_fetch_news, mock_get_option, mock_get_price, mock_send_alert):
        # --- Setup Mocks ---
        main_global_sent_hashes.clear() # Clear the global deque before each run of this test
        mock_polarity_scores.return_value = {'compound': 0.95} # Ensure high sentiment

        # 1. Mock fetch_marketaux_news
        mock_news_article = {
            'title': 'EXTREMELY POSITIVE NEWS for NVDA!', # Slightly more emphatic
            'description': 'NVDA stock is definitely going to the moon, amazing results!',
            'source': 'marketaux', # Ensure all expected fields by main.py are present
            'url': 'http://example.com/news',
            'image_url': 'http://example.com/image.png',
            'language': 'en',
            'published_at': '2023-01-01T12:00:00Z',
            'uuid': 'test-uuid-1',
            'entities': [{'symbol': 'NVDA', 'name': 'NVIDIA Corp'}] # Assuming structure
        }
        # The structure of entities in the actual API might be different.
        # The current main.py code checks `if ticker in content:` where content is title + description.
        # It does not directly use article.get('entities').
        # For the test to work as intended with `if ticker in content`, 'NVDA' must be in title or description.
        mock_fetch_news.return_value = [mock_news_article]

        # 2. Mock Polygon price and option data
        mock_get_price.return_value = 300.0  # Mock stock price for NVDA
        mock_get_option.return_value = (290.0, 5.5)  # Mock strike_price, ask_price

        # 3. Mock 'open' for reading alerts.json (initially empty)
        # The mock_open by default will handle read. For json.load, we need to ensure it returns a valid list.
        mock_file_open.side_effect = [
            mock_open(read_data='[]').return_value,  # For initial read of alerts.json
            mock_open().return_value  # For writing alerts.json
        ]
        
        # --- Execute Function ---
        # Make sure TICKERS includes 'NVDA' and set a testable SENTIMENT_THRESHOLD
        with patch('main.TICKERS', ['NVDA', 'TSLA']): # Ensure NVDA is in the list for the test
            # SENTIMENT_THRESHOLD will be effectively bypassed by mocking polarity_scores directly above SENTIMENT_THRESHOLD
            # but we keep it patched to a low value for consistency if the mock above was removed.
            with patch('main.SENTIMENT_THRESHOLD', 0.1): 
                 scan_and_alert() # Call the function

        # --- Assertions ---
        # 1. Check if news was fetched
        mock_fetch_news.assert_called_once()

        # 2. Check if price and option data were fetched for NVDA
        mock_get_option.assert_called_with('NVDA') # Check this first
        mock_get_price.assert_called_with('NVDA')

        # 3. Check if Telegram alert was sent
        mock_send_alert.assert_called_once()
        alert_message_args = mock_send_alert.call_args[0][0] # Get the first positional argument of the call
        self.assertIn("Trade Alert: NVDA", alert_message_args)
        self.assertIn("EXTREMELY POSITIVE NEWS for NVDA!", alert_message_args)
        self.assertIn("$300.00", alert_message_args) # Market Price
        self.assertIn("$290.00", alert_message_args) # Option Strike
        self.assertIn("$5.50", alert_message_args)   # Ask Price

        # 4. Check if alerts.json was written to
        # mock_file_open should have been called twice (read, then write)
        self.assertEqual(mock_file_open.call_count, 2)

        # Check the content written by json.dump
        mock_json_dump.assert_called_once()
        written_data_args = mock_json_dump.call_args[0][0] # First arg of json.dump
        self.assertEqual(len(written_data_args), 1)
        self.assertEqual(written_data_args[0]['ticker'], 'NVDA')
        self.assertEqual(written_data_args[0]['headline'], 'EXTREMELY POSITIVE NEWS for NVDA!')
        # The sentiment stored will be the mocked one (0.95), check against that.
        self.assertEqual(written_data_args[0]['sentiment'], 0.95)

if __name__ == '__main__':
    unittest.main()
