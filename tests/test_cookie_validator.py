import os
import sys
import tempfile

# Make sure code can import from parent directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Also ensure project root is in path
if os.getcwd() not in sys.path:
    sys.path.append(os.getcwd())

try:
    from shorts_generator.downloader import validate_youtube_cookies
except ImportError:
    # Fallback if executed from inside tests folder
    sys.path.append(os.path.abspath(".."))
    from shorts_generator.downloader import validate_youtube_cookies

def test_expired_cookies():
    print("--- TEST 1: Expired Cookies ---")
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(".youtube.com\tTRUE\t/\tTRUE\t1609459200\tSID\texpired_val\n")
        f.write(".youtube.com\tTRUE\t/\tTRUE\t1609459200\tHSID\texpired_val\n")
        f_path = f.name
        
    try:
        os.environ["YOUTUBE_COOKIES_FILE"] = f_path
        if "YOUTUBE_COOKIES" in os.environ: 
            # Save real one temporarily to restore later
            real_cookies = os.environ.pop("YOUTUBE_COOKIES")
        else:
            real_cookies = None

        is_valid, msg = validate_youtube_cookies()
        print(f"Validator Result: is_valid={is_valid}, msg={msg}")
        
        assert is_valid == False, "FAILED: Should be marked invalid for expired cookies."
        assert "Expired cookies detected" in msg, f"FAILED: Wrong message: {msg}"
        print("✅ TEST 1 PASSED.")
        if real_cookies: os.environ["YOUTUBE_COOKIES"] = real_cookies
    finally:
        os.unlink(f_path)

def test_missing_auth():
    print("\n--- TEST 2: Missing Critical Auth Rows ---")
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(".youtube.com\tTRUE\t/\tTRUE\t2000000000\tYSC\tgeneric_tracking\n")
        f_path = f.name
        
    try:
        os.environ["YOUTUBE_COOKIES_FILE"] = f_path
        if "YOUTUBE_COOKIES" in os.environ:
             backup = os.environ.pop("YOUTUBE_COOKIES")
        else:
             backup = None
             
        is_valid, msg = validate_youtube_cookies()
        print(f"Validator Result: is_valid={is_valid}, msg={msg}")
        
        assert is_valid == False, "FAILED: Should be invalid with no auth rows."
        assert "no valid YouTube auth cookies" in msg, f"FAILED: Wrong message: {msg}"
        print("✅ TEST 2 PASSED.")
        if backup: os.environ["YOUTUBE_COOKIES"] = backup
    finally:
        os.unlink(f_path)

def test_valid_cookies():
    print("\n--- TEST 3: Valid (Session/Future) Cookies ---")
    import time
    future = int(time.time() + 86400) 
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(f".youtube.com\tTRUE\t/\tTRUE\t{future}\tSID\tfresh_val\n")
        f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tHSID\tsession_val\n") 
        f_path = f.name
        
    try:
        os.environ["YOUTUBE_COOKIES_FILE"] = f_path
        if "YOUTUBE_COOKIES" in os.environ:
             backup = os.environ.pop("YOUTUBE_COOKIES")
        else:
             backup = None
             
        is_valid, msg = validate_youtube_cookies()
        print(f"Validator Result: is_valid={is_valid}, msg={msg}")
        
        assert is_valid == True, f"FAILED: Expected VALID but got invalid: {msg}"
        assert "VALID" in msg, "FAILED message does not contain VALID"
        print("✅ TEST 3 PASSED.")
        if backup: os.environ["YOUTUBE_COOKIES"] = backup
    finally:
        os.unlink(f_path)

if __name__ == "__main__":
    try:
        # Temporarily disable printing the temp file location inside validate helper to reduce noise in CI
        orig_file = os.environ.get("YOUTUBE_COOKIES_FILE")
        
        test_expired_cookies()
        test_missing_auth()
        test_valid_cookies()
        print("\n🎉 ALL UNIT TESTS PASSED SUCCESSFULLY!")
        
        if orig_file: os.environ["YOUTUBE_COOKIES_FILE"] = orig_file
        else: os.environ.pop("YOUTUBE_COOKIES_FILE", None)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
