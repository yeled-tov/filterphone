"""
FilterPhone — מנוע סינון תמונות (היברידי)
"""
import mitmproxy.http
from mitmproxy import ctx
import os
import time
from datetime import datetime
from config import MIN_IMAGE_SIZE, MAX_IMAGE_SIZE
from cache_manager import get_cached_result, set_cached_result, get_filter_level, increment_stat
from yolo_detector import detect_persons
from skin_detector import detect_skin_ratio
from nsfw_detector import analyze_nudity

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BROWSER_DOMAINS = set()

def load_browser_domains():
    path = os.path.join(SCRIPT_DIR, "browser_domains.txt")
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    BROWSER_DOMAINS.add(line.lower())

load_browser_domains()

def log_action(client_ip, domain, action, details, elapsed_ms):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{client_ip}] [{domain}] [{action}] [{details}] [{elapsed_ms}ms]", flush=True)

def is_image_response(flow):
    content_type = flow.response.headers.get("content-type", "")
    return "image/" in content_type.lower()

def decide_action(yolo_result, nudity_result, filter_level):
    """
    מחזיר 3 ערכים: (should_block, should_segment, reason)
    """
    has_face_female = nudity_result.get("has_face_female", False)
    has_exposed_female = nudity_result.get("has_exposed_female", False)
    has_covered_female = nudity_result.get("has_covered_female_parts", False)
    has_explicit = nudity_result.get("has_explicit_nudity", False)
    
    skin_ratio = yolo_result.get("skin_ratio", 0.0)

    # --- רמה 3: בסיסית בלבד ---
    if filter_level == 3:
        if has_explicit or has_exposed_female:
            return True, False, "explicit nudity"
        return False, False, "level 3 - pass"

    # --- רמה 2: גמישה (סגמנטציה) ---
    if filter_level == 2:
        if has_explicit:
            return True, False, "explicit nudity" # רק פורנו מובהק נחסם קומפלט
        
        # כל השאר (כולל בגדי ים ועור מעל 5%) נשלח לסיגמנטציה (ציור שחור)
        if skin_ratio > 0.05 or has_exposed_female:
            return False, True, "level 2 - segmentation"
        return False, False, "level 2 - clean"

    # --- רמה 1: קשוחה מפלדה ---
    if filter_level == 1:
        # דברים חמורים עפים מיד
        if has_explicit or has_exposed_female:
            return True, False, "explicit nudity"
        
        # חוקי העור החדשים
        if skin_ratio > 0.13:
            return True, False, f"skin ratio > 13% ({skin_ratio:.2f})"
        elif skin_ratio >= 0.07:
            return False, True, f"segmentation needed (skin {skin_ratio:.2f})"
        
        # גיבוי - דברים נשיים אחרים
        if has_face_female:
            return True, False, "face female"
        if has_covered_female:
            return True, False, "female body parts (covered)"
        
        # זיהוי גיבוי של YOLO
        persons = yolo_result.get("persons", [])
        for p in persons:
            shape = p.get("body_shape", "uncertain")
            if shape in ("female", "female_no_head"):
                return True, False, "yolo female detected"

    return False, False, f"clean (skin={skin_ratio:.1%})"

class FilterPhoneAddon:
    def response(self, flow: mitmproxy.http.HTTPFlow):
        start_time = time.time()
        client_ip = flow.client_conn.peername[0] if flow.client_conn.peername else "unknown"
        host = flow.request.pretty_host

        try:
            if not is_image_response(flow):
                return
            content = flow.response.content
            if not content or len(content) < MIN_IMAGE_SIZE:
                return
            if len(content) > MAX_IMAGE_SIZE:
                return

            increment_stat("images_scanned")
            filter_level = get_filter_level(client_ip)

            if filter_level == 0:
                return

            # קאש מחולק לפי רמות (כרגע מבוטל לחלוטין מתוך מנהל הקאש!)
            cached = get_cached_result(content, filter_level)
            if cached is not None:
                increment_stat("cache_hits")
                if cached.get("block", False):
                    flow.response.status_code = 403
                    flow.response.reason = "Blocked by FilterPhone"
                    flow.response.content = b""
                    flow.response.headers["content-type"] = "text/plain"
                    flow.response.headers["content-length"] = "0"
                    flow.response.headers["cache-control"] = "no-store, no-cache, must-revalidate"
                    flow.response.headers["x-filterphone"] = "blocked"
                    increment_stat("images_blocked")
                    log_action(client_ip, host, "CACHED-BLOCK", f"level {filter_level}", int((time.time() - start_time) * 1000))
                else:
                    log_action(client_ip, host, "CACHED-PASS", f"level {filter_level}", int((time.time() - start_time) * 1000))
                return

            nudity_result = analyze_nudity(content)
            
            # תמיד מריצים זיהוי עור ויולו, כי אנחנו צריכים את האחוזים המדויקים לרמות השונות
            yolo_result = detect_persons(content)
            if not yolo_result.get("has_person", False):
                yolo_result["skin_ratio"] = detect_skin_ratio(content)

            should_block, should_segment, reason = decide_action(yolo_result, nudity_result, filter_level)

            segmentation_succeeded = False
            if should_segment:
                try:
                    from skin_segmenter import segment_skin
                    dets = nudity_result.get("detections", [])
                    seg_bytes = segment_skin(content, dets)
                    if seg_bytes:
                        flow.response.content = seg_bytes
                        content = seg_bytes
                        segmentation_succeeded = True
                except Exception as e:
                    ctx.log.error(f"[FilterPhone] segment_skin error: {e}")

            # שמירה לקאש (הפונקציה מתעלמת כרגע כי המתג כבוי)
            set_cached_result(content, {
                "block": should_block,
                "level": filter_level,
                "persons": yolo_result.get("num_persons", 0),
            }, filter_level)

            elapsed = int((time.time() - start_time) * 1000)
            
            if should_block:
                flow.response.status_code = 403
                flow.response.reason = "Blocked by FilterPhone"
                flow.response.content = b""
                flow.response.headers["content-type"] = "text/plain"
                flow.response.headers["content-length"] = "0"
                flow.response.headers["cache-control"] = "no-store, no-cache, must-revalidate"
                flow.response.headers["x-filterphone"] = "blocked"
                increment_stat("images_blocked")
                log_action(client_ip, host, "BLOCKED", reason, elapsed)
            elif should_segment and segmentation_succeeded:
                increment_stat("images_segmented")
                log_action(client_ip, host, "SEGMENTED", reason, elapsed)
            elif should_segment and not segmentation_succeeded:
                increment_stat("images_passed")
                log_action(client_ip, host, "PASS-NO-PERSON", f"{reason} (no person)", elapsed)
            else:
                increment_stat("images_passed")
                log_action(client_ip, host, "PASSED", reason, elapsed)

        except Exception as e:
            elapsed = int((time.time() - start_time) * 1000)
            log_action(client_ip, host, "ERROR", str(e), elapsed)
            ctx.log.error(f"[FilterPhone] error: {e}")

addons = [FilterPhoneAddon()]
