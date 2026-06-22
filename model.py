import os
import cv2
import pickle
import time
import numpy as np
import face_recognition
from sklearn.neighbors import KNeighborsClassifier, NearestNeighbors
from sklearn.utils import shuffle

MODEL_PATH = "face_model.pkl"
CACHE_PATH = "embeddings_cache.pkl"
EMBED_DB = "embeddings_db.npz"  # stores arrays X and y for fast incremental updates

# ==============================
# 🔥 TRÍCH XUẤT EMBEDDING
# ==============================
def extract_embedding(img):
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    boxes = face_recognition.face_locations(rgb, model="hog")  # "hog" nhanh hơn "cnn"
    if len(boxes) == 0:
        return None
    encodings = face_recognition.face_encodings(rgb, boxes, num_jitters=1)
    if len(encodings) == 0:
        return None
    return encodings[0]


def extract_from_stream(stream, use_cnn=True):
    try:
        stream.seek(0)
        data = stream.read()
        arr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Dùng CNN lúc nhận diện thật để chính xác hơn
        detection_model = "cnn" if use_cnn else "hog"
        boxes = face_recognition.face_locations(rgb, model=detection_model)
        if len(boxes) == 0:
            return None
        encodings = face_recognition.face_encodings(rgb, boxes, num_jitters=1)
        if len(encodings) == 0:
            return None
        return encodings[0]
    except:
        return None


# ==============================
# 🔥 HUẤN LUYỆN
# ==============================
def train_model(dataset_dir, progress_callback=None):
    # Load cache embedding cũ nếu có
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "rb") as f:
            cache = pickle.load(f)
        print(f"📦 Đã load cache: {len(cache)} embedding")
    else:
        cache = {}

    X, y = [], []
    student_dirs = [
        d for d in os.listdir(dataset_dir)
        if os.path.isdir(os.path.join(dataset_dir, d))
    ]

    if len(student_dirs) == 0:
        print("❌ Không tìm thấy thư mục sinh viên nào!")
        return False

    total = max(1, len(student_dirs))
    processed = 0

    for sid in student_dirs:
        folder = os.path.join(dataset_dir, sid)
        files = [
            f for f in os.listdir(folder)
            if f.lower().endswith((".jpg", ".png", ".jpeg"))
        ]
        valid = 0
        for fn in files:
            path = os.path.join(folder, fn)

            # Dùng cache nếu đã tính embedding rồi
            if path in cache:
                emb = cache[path]
            else:
                img = cv2.imread(path)
                emb = extract_embedding(img)
                if emb is None:
                    continue
                cache[path] = emb  # Lưu vào cache

            X.append(emb)
            y.append(sid)
            valid += 1

        print(f"👉 {sid}: {valid} ảnh hợp lệ")
        processed += 1
        if progress_callback:
            progress_callback(int((processed / total) * 70), f"{processed}/{total}")

    # Lưu cache lại sau khi xử lý xong
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(cache, f)
    print(f"💾 Đã lưu cache: {len(cache)} embedding")

    if len(X) == 0:
        print("❌ Không có embedding nào!")
        return False

    if len(set(y)) < 2:
        print("⚠ Chỉ có 1 người — vẫn train (mode test)")
        # KHÔNG return False

    # Debug thêm
    print(f"👉 Tổng embedding: {len(X)}")
    print(f"👉 Số người: {len(set(y))}")

    X = np.array(X)
    y = np.array(y)
    X, y = shuffle(X, y, random_state=42)

    if progress_callback:
        progress_callback(80, "Đang huấn luyện...")

    # Tự động chọn n_neighbors phù hợp (nhiều ảnh thì vote nhiều hơn)
    n_neighbors = min(7, len(X))
    clf = KNeighborsClassifier(n_neighbors=n_neighbors, metric="euclidean", weights="distance")
    clf.fit(X, y)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)

    # Lưu embeddings DB (X, y) để có thể train incremental sau này
    try:
        np.savez(EMBED_DB, X=X, y=y)
    except Exception as e:
        print("⚠️ Không lưu được EMBED_DB:", e)

    if progress_callback:
        progress_callback(100, "Hoàn tất")

    print("✅ Đã lưu mô hình!")
    return True


def train_from_embeddings(X, y, progress_callback=None):
    """Train model directly from given embeddings arrays and save model and db."""
    if len(X) == 0:
        print("❌ train_from_embeddings: no data")
        return False

    X = np.array(X)
    y = np.array(y)
    X, y = shuffle(X, y, random_state=42)

    if progress_callback:
        progress_callback(80, "Đang huấn luyện...")

    n_neighbors = min(7, len(X))
    clf = KNeighborsClassifier(n_neighbors=n_neighbors, metric="euclidean", weights="distance")
    clf.fit(X, y)

    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(clf, f)
        np.savez(EMBED_DB, X=X, y=y)
    except Exception as e:
        print("🔥 train_from_embeddings save error:", e)
        return False

    if progress_callback:
        progress_callback(100, "Hoàn tất")

    print("✅ train_from_embeddings: model saved")
    return True


def load_embeddings_db():
    """Return (X, y) arrays from EMBED_DB or (None, None) if missing."""
    if not os.path.exists(EMBED_DB):
        return None, None
    try:
        data = np.load(EMBED_DB, allow_pickle=True)
        return data['X'], data['y']
    except Exception as e:
        print("⚠️ load_embeddings_db error:", e)
        return None, None


def add_embeddings_for_student(student_id, image_streams, verify=True, min_self_thresh=0.45, min_other_thresh=0.65, margin=0.08):
    """Add embeddings (from streams) for student_id safely.

    - image_streams: list of file-like objects (seekable) or OpenCV images
    - verify: perform cross-checks to avoid mislabeling
    Returns (success:bool, message:str)
    """
    new_embs = []

    # compute embeddings from provided streams
    for idx, s in enumerate(image_streams):
        try:
            # if it's a file-like stream
            if hasattr(s, 'read'):
                s.seek(0)
                data = s.read()
                arr = np.frombuffer(data, np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            else:
                # assume it's already an image array
                img = s

            emb = extract_embedding(img)
            if emb is not None:
                new_embs.append(emb)
        except Exception as e:
            print(f"⚠️ add_embeddings_for_student: skip image {idx} error:", e)

    if len(new_embs) == 0:
        return False, "Không có embedding hợp lệ từ ảnh tải lên"

    X_old, y_old = load_embeddings_db()
    if X_old is None:
        # no existing DB — accept if new embeddings are valid and not too close to each other
        X_new = np.array(new_embs)
        y_new = np.array([str(student_id)] * len(new_embs))
        ok = train_from_embeddings(np.vstack([X_new]) if X_new.ndim==2 else X_new, y_new)
        return (True, "Thêm embedding thành công và huấn luyện lại mô hình") if ok else (False, "Huấn luyện lại thất bại")

    # Build NN index on old embeddings
    try:
        n_neighbors = min(3, len(X_old))
        nbrs = NearestNeighbors(n_neighbors=n_neighbors, metric='euclidean').fit(X_old)
        dists, idxs = nbrs.kneighbors(new_embs)
    except Exception as e:
        print("⚠️ NearestNeighbors build error:", e)
        return False, "Lỗi khi kiểm tra embeddings"

    # Map indices to labels
    y_old = np.array(y_old).astype(str)

    # Verification logic
    for i, emb in enumerate(new_embs):
        neigh_dists = dists[i]
        neigh_idxs = idxs[i]
        neigh_labels = y_old[neigh_idxs]

        # distance to closest embedding of same student (if exists)
        same_mask = (y_old == str(student_id))
        if same_mask.any():
            # compute mean distance to self embeddings
            self_dists = np.linalg.norm(X_old[same_mask] - emb, axis=1)
            avg_self = float(np.mean(self_dists))
        else:
            avg_self = float('inf')

        # min distance to other students
        other_mask = (y_old != str(student_id))
        if other_mask.any():
            other_dists = np.linalg.norm(X_old[other_mask] - emb, axis=1)
            min_other = float(np.min(other_dists))
        else:
            min_other = float('inf')

        # Decide acceptance
        if verify:
            # if we have self examples, ensure emb is close to self and far from others
            if avg_self != float('inf'):
                if not (avg_self <= min_self_thresh and min_other >= min_other_thresh and (min_other - avg_self) >= margin):
                    return False, f"Embedding thứ {i+1} không an toàn: avg_self={avg_self:.3f}, min_other={min_other:.3f}"
            else:
                # new student: ensure min_other is sufficiently large
                if not (min_other >= min_other_thresh):
                    return False, f"Embedding thứ {i+1} quá giống người khác (dist={min_other:.3f})"

    # If all embeddings pass, append to DB
    try:
        X_new = np.vstack([X_old, np.array(new_embs)])
        y_new = np.hstack([y_old, np.array([str(student_id)] * len(new_embs))])
        # Save cache keys for these images with generated ids (optional)
        # Train new model from embeddings
        ok = train_from_embeddings(X_new, y_new)
        if ok:
            # also update embeddings_cache for traceability (use stream keys)
            try:
                if os.path.exists(CACHE_PATH):
                    with open(CACHE_PATH, 'rb') as f:
                        cache = pickle.load(f)
                else:
                    cache = {}
                # add generated keys
                for i, emb in enumerate(new_embs):
                    key = f"stream:{student_id}:{int(time.time())}:{i}"
                    cache[key] = emb
                with open(CACHE_PATH, 'wb') as f:
                    pickle.dump(cache, f)
            except Exception as e:
                print("⚠️ update cache error:", e)

            return True, "Thêm embedding thành công và huấn luyện lại mô hình"
        else:
            return False, "Huấn luyện lại thất bại"
    except Exception as e:
        print("🔥 append/train error:", e)
        return False, "Lỗi khi thêm embedding" 


# ==============================
# 🔥 TẢI MÔ HÌNH
# ==============================
def load_model():
    if not os.path.exists(MODEL_PATH):
        return None
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


# ==============================
# 🔥 DỰ ĐOÁN
# ==============================
def predict_stream(stream, threshold=0.4):
    clf = load_model()
    if clf is None:
        return "no_model", 0.0

    emb = extract_from_stream(stream, use_cnn=False)  # HOG nhanh hơn
    if emb is None:
        return "no_face", 0.0

    distances, _ = clf.kneighbors([emb], n_neighbors=1)
    dist = distances[0][0]

    print(f"🔍 Distance: {dist:.4f} | Threshold: {threshold}")

    if dist > threshold:
        return "unknown", 0.0

    label = clf.predict([emb])[0]
    confidence = round(float(max(0.0, 1 - (dist / threshold))), 4)
    return label, confidence