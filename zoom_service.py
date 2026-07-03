import base64
import requests
import time
from config import ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_MEETING_ID

class ZoomService:
    """
    Service layer interacting with the Zoom API using Server-to-Server OAuth.
    """
    def __init__(self):
        self._access_token = None
        self._token_expires_at = 0
        self._cached_creds = None
        
    @property
    def account_id(self):
        import storage
        return storage.get_setting("zoom_account_id", ZOOM_ACCOUNT_ID)
        
    @property
    def client_id(self):
        import storage
        return storage.get_setting("zoom_client_id", ZOOM_CLIENT_ID)
        
    @property
    def client_secret(self):
        import storage
        return storage.get_setting("zoom_client_secret", ZOOM_CLIENT_SECRET)
        
    @property
    def meeting_id(self):
        import storage
        return storage.get_setting("zoom_meeting_id", ZOOM_MEETING_ID)

    def _get_access_token(self) -> str:
        """
        Retrieves the OAuth access token. Caches it based on expiration time.
        """
        current_creds = (self.account_id, self.client_id, self.client_secret)
        # If token is still valid (with a 60-second safety margin) and creds match (or if _cached_creds is None, e.g., in unit tests), return cache
        if self._access_token and (self._cached_creds == current_creds or self._cached_creds is None) and time.time() < self._token_expires_at - 60:
            self._cached_creds = current_creds
            return self._access_token

        url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={self.account_id}"
        
        # Prepare Base64 Basic Authentication header
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_creds = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_creds}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # S2S OAuth requires a POST request
        response = requests.post(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch Zoom OAuth token (Status {response.status_code}): {response.text}"
            )
            
        data = response.json()
        self._access_token = data["access_token"]
        self._cached_creds = current_creds
        # Default expires_in is 3599 seconds (1 hour)
        expires_in = data.get("expires_in", 3599)
        self._token_expires_at = time.time() + expires_in
        return self._access_token

    def get_registrant_id_by_email(self, email: str) -> str | None:
        """
        Searches the meeting registrants list by email to retrieve their Zoom Registrant ID.
        Checks across 'pending', 'approved', and 'denied' statuses.
        """
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Query statuses to search for the user
        for status in ["pending", "approved", "denied"]:
            params = {
                "status": status,
                "page_size": 100
            }
            next_page_token = ""
            
            while True:
                if next_page_token:
                    params["next_page_token"] = next_page_token
                    
                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code != 200:
                    # Log or skip, try the next status
                    break
                    
                data = response.json()
                registrants = data.get("registrants", [])
                for reg in registrants:
                    if reg.get("email", "").lower() == email.lower():
                        return reg.get("id")
                
                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break
                    
        return None

    def list_registrants(self, status: str = "pending") -> list[dict]:
        """
        Retrieves all registrants for the active meeting with a given status.
        """
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        registrants = []
        params = {
            "status": status,
            "page_size": 100
        }
        next_page_token = ""
        
        try:
            while True:
                if next_page_token:
                    params["next_page_token"] = next_page_token
                    
                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code != 200:
                    break
                    
                data = response.json()
                registrants.extend(data.get("registrants", []))
                
                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break
        except Exception:
            pass
            
        return registrants

    def is_email_registered_on_zoom(self, email: str) -> bool:
        """
        Validates if the email is registered under the active Zoom meeting.
        """
        try:
            return self.get_registrant_id_by_email(email) is not None
        except Exception:
            return False

    def update_registrant_status(self, email: str, action: str) -> bool:
        """
        Updates a registrant's meeting status to approved or denied.
        
        Parameters:
        - email: The registrant's email.
        - action: Either 'approve' or 'deny'
        """
        if action not in ["approve", "deny"]:
            raise ValueError("Action must be either 'approve' or 'deny'")
            
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants/status"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # First, try to resolve the registrant ID for robustness
        registrant_id = self.get_registrant_id_by_email(email)
        
        registrant_payload = {"email": email}
        if registrant_id:
            registrant_payload["id"] = registrant_id
            
        payload = {
            "action": action,
            "registrants": [registrant_payload]
        }
        
        response = requests.put(url, headers=headers, json=payload, timeout=10)
        
        # Zoom API returns 204 No Content on success
        if response.status_code not in [200, 204]:
            err_msg = response.text
            try:
                err_data = response.json()
                err_msg = err_data.get("message", err_msg)
            except Exception:
                pass
            raise Exception(f"Zoom API Error (Status {response.status_code}): {err_msg}")
            
        return True

    def update_registrant_name(self, email: str, new_name: str) -> bool:
        """
        Updates the registrant's display name on Zoom by re-submitting their details.
        Zoom's API automatically merges duplicate registrations by email and updates the name.
        """
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Split the new_name into first_name and last_name
        parts = new_name.strip().split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else "."
        
        payload = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code not in [200, 201]:
            err_msg = response.text
            try:
                err_data = response.json()
                err_msg = err_data.get("message", err_msg)
            except Exception:
                pass
            raise Exception(f"Zoom API Error (Status {response.status_code}): {err_msg}")
            
        return True

    def get_custom_questions(self) -> dict:
        """
        Retrieves standard and custom questions configured for the Zoom meeting.
        """
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants/questions"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch Zoom registration questions (Status {response.status_code}): {response.text}")
            
        return response.json()

    def register_registrant(self, email: str, first_name: str, last_name: str, custom_questions: list = None) -> dict:
        """
        Submits a new registrant to Zoom.
        Returns a dict containing 'join_url' and 'registrant_id'.
        """
        token = self._get_access_token()
        url = f"https://api.zoom.us/v2/meetings/{self.meeting_id}/registrants"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "email": email.strip().lower(),
            "first_name": first_name.strip(),
            "last_name": last_name.strip()
        }
        if custom_questions:
            payload["custom_questions"] = custom_questions
            
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code not in [200, 201]:
            err_msg = response.text
            try:
                err_data = response.json()
                err_msg = err_data.get("message", err_msg)
            except Exception:
                pass
            raise Exception(f"Zoom Registration Error (Status {response.status_code}): {err_msg}")
            
        data = response.json()
        return {
            "registrant_id": data.get("id"),
            "join_url": data.get("join_url")
        }
