import os
import shutil
import unittest

# Set up mock environment variables for validation before importing config
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
os.environ["ADMIN_CHAT_ID"] = "987654321"
os.environ["ZOOM_ACCOUNT_ID"] = "mock_account_id"
os.environ["ZOOM_CLIENT_ID"] = "mock_client_id"
os.environ["ZOOM_CLIENT_SECRET"] = "mock_client_secret"
os.environ["ZOOM_MEETING_ID"] = "123 456 7890"  # contains spaces to verify stripping
os.environ["ZOOM_REGISTRATION_LINK"] = "https://zoom.us/meeting/register/1234567890"
os.environ["DATABASE_PATH"] = "test_database.db"
os.environ["DATABASE_URL"] = "" # force test environment to SQLite

import config
import storage
import zoom_service
import app

class TestSystemScaffold(unittest.TestCase):
    
    def setUp(self):
        # Initialize/clean the test database file
        if os.path.exists("test_database.db"):
            os.remove("test_database.db")
        storage.init_db()

    def tearDown(self):
        # Remove the test database file
        if os.path.exists("test_database.db"):
            os.remove("test_database.db")

    def test_config_parsing(self):
        """
        Verify config variables parse and validate correctly.
        """
        self.assertEqual(config.ADMIN_CHAT_ID, 987654321)
        self.assertEqual(config.ZOOM_MEETING_ID, "1234567890")
        self.assertEqual(config.DATABASE_PATH, "test_database.db")
        self.assertTrue(config.ZOOM_REGISTRATION_LINK.endswith("1234567890"))

    def test_database_operations(self):
        """
        Verify SQLite storage schema operations: insert, update, history, blacklist and report metrics.
        """
        email = "alice@example.com"
        tg_id = 111222333
        tg_username = "alice_tg"
        zoom_name = "Alice Zoom"
        meeting_id = config.ZOOM_MEETING_ID

        # 1. Test adding initial submission
        sub_id = storage.add_submission(email, tg_id, zoom_name, tg_username, meeting_id, "Pending")
        self.assertIsNotNone(sub_id)
        
        # Verify status is 'Pending'
        status = storage.get_user_status(email)
        self.assertEqual(status, "Pending")
        
        # Verify submissions history contains 1 record
        history = storage.get_submissions_by_email(email)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["submitted_zoom_name"], zoom_name)

        # 2. Test blacklisting user
        success = storage.update_user_status(email, "Blacklisted", "Spam applicant")
        self.assertTrue(success)
        
        status = storage.get_user_status(email)
        self.assertEqual(status, "Blacklisted")
        
        # Check behavioral notes
        user = storage.get_user_by_email(email)
        self.assertIn("Spam applicant", user["behavior_notes"])

        # 3. Test submitting again as a blacklisted user
        # (Status should remain blacklisted, name count should increase)
        new_sub_id = storage.add_submission(email, tg_id, "Alice Suspicious Name", tg_username, meeting_id, "Pending")
        self.assertIsNotNone(new_sub_id)
        
        # Status must still be Blacklisted
        status = storage.get_user_status(email)
        self.assertEqual(status, "Blacklisted")
        
        # History must contain 2 submissions
        history = storage.get_submissions_by_email(email)
        self.assertEqual(len(history), 2)
        
        # 4. Check admin report statistics
        report = storage.get_admin_report_data()
        self.assertEqual(report["total_users"], 1)
        self.assertEqual(report["status_counts"]["Blacklisted"], 1)
        self.assertEqual(report["total_submissions"], 2)
        
        # Verify suspicious activities detects duplicate email with multiple Zoom names
        self.assertEqual(len(report["suspicious_users"]), 1)
        self.assertEqual(report["suspicious_users"][0]["registered_email"], email)
        self.assertEqual(report["suspicious_users"][0]["name_count"], 2)

    def test_zoom_service_init(self):
        """
        Verify ZoomService initializes successfully.
        """
        zs = zoom_service.ZoomService()
        self.assertEqual(zs.meeting_id, "1234567890")

    def test_zoom_service_mocked_registrants(self):
        """
        Verify registrant search and validation using mocked requests.
        """
        import unittest.mock as mock
        import time
        
        zs = zoom_service.ZoomService()
        
        # Mock access token retrieval
        zs._access_token = "mock_access_token"
        zs._token_expires_at = time.time() + 3600
        
        with mock.patch("requests.get") as mock_get:
            # 1. Mock response for listing/searching registrants
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "registrants": [
                    {"id": "registrant_123", "email": "valid_user@example.com", "first_name": "Valid", "last_name": "User"}
                ],
                "next_page_token": ""
            }
            mock_get.return_value = mock_response
            
            # Verify lookup returns ID for registered email
            reg_id = zs.get_registrant_id_by_email("valid_user@example.com")
            self.assertEqual(reg_id, "registrant_123")
            
            # Verify validation helper returns True
            self.assertTrue(zs.is_email_registered_on_zoom("valid_user@example.com"))
            
            # Verify validation helper returns False for non-registered email
            mock_response_empty = mock.Mock()
            mock_response_empty.status_code = 200
            mock_response_empty.json.return_value = {
                "registrants": [],
                "next_page_token": ""
            }
            mock_get.return_value = mock_response_empty
            self.assertFalse(zs.is_email_registered_on_zoom("unregistered@example.com"))

    def test_admin_authorization(self):
        """
        Verify admin rights authorization CRUD functions.
        """
        # Super-admin should be authorized by default
        self.assertTrue(storage.is_admin(987654321))
        
        # Random user should not be authorized
        self.assertFalse(storage.is_admin(111222))
        
        # Authorize a new admin
        storage.add_admin(111222, "secondary_admin")
        self.assertTrue(storage.is_admin(111222))
        
        # Verify lists
        admins = storage.get_admins()
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["telegram_id"], 111222)
        
        # Revoke rights
        storage.remove_admin(111222)
        self.assertFalse(storage.is_admin(111222))

    def test_app_handlers(self):
        """
        Verify app.py structure runs and conversation states are initialized.
        """
        # Ensure email validator utility functions correctly
        self.assertTrue(app.is_valid_email("test@domain.com"))
        self.assertFalse(app.is_valid_email("invalid-email"))

    def test_zoom_service_custom_methods(self):
        """
        Verify ZoomService.get_custom_questions and register_registrant.
        """
        import unittest.mock as mock
        import time
        zs = zoom_service.ZoomService()
        zs._access_token = "mock_access_token"
        zs._token_expires_at = time.time() + 3600
        
        with mock.patch("requests.get") as mock_get:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "custom_questions": [{"title": "Job Title", "type": "short", "required": True}]
            }
            mock_get.return_value = mock_response
            
            questions = zs.get_custom_questions()
            self.assertEqual(len(questions["custom_questions"]), 1)
            self.assertEqual(questions["custom_questions"][0]["title"], "Job Title")
            
        with mock.patch("requests.post") as mock_post:
            mock_response = mock.Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {
                "id": "registrant_999",
                "join_url": "https://zoom.us/j/999"
            }
            mock_post.return_value = mock_response
            
            reg = zs.register_registrant("test@example.com", "Test", "User", [{"title": "Job Title", "value": "Developer"}])
            self.assertEqual(reg["registrant_id"], "registrant_999")
            self.assertEqual(reg["join_url"], "https://zoom.us/j/999")

    def test_web_server_endpoints(self):
        """
        Verify FastAPI web server endpoints.
        """
        from fastapi.testclient import TestClient
        import web_server
        import unittest.mock as mock
        
        client = TestClient(web_server.app)
        
        # 1. Test Health check
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        
        # 2. Test Questions endpoint
        with mock.patch("zoom_service.ZoomService.get_custom_questions") as mock_get_questions:
            mock_get_questions.return_value = {
                "custom_questions": [{"title": "Industry", "type": "short"}]
            }
            response = client.get("/api/questions")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["custom_questions"][0]["title"], "Industry")

if __name__ == "__main__":
    unittest.main()
