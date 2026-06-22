const saveInfoBtn = document.getElementById("saveInfoBtn");
const startCaptureBtn = document.getElementById("startCaptureBtn");
const addStudentBtn = document.getElementById("addStudentBtn");
const video = document.getElementById("video");
const captureStatus = document.getElementById("captureStatus");
const progressBar = document.getElementById("progressBar");


let student_id = null;
let captured = 0;
const maxImages = 50;
let images = [];
let stream = null;


/* =========================
   SAVE STUDENT INFO
========================= */
document.getElementById("studentForm").addEventListener("submit", async (e) => {
  e.preventDefault();


  const fd = new FormData(e.target);


  const res = await fetch("/add_student", {
    method: "POST",
    body: fd
  });


  const j = await res.json();


  if (!res.ok) {
    alert(j.error || "Save failed");
    return;
  }


  student_id = j.student_id;


  alert("Saved ID: " + student_id);


  startCaptureBtn.disabled = false;
});


/* =========================
   START CAMERA
========================= */
startCaptureBtn.addEventListener("click", async () => {
  startCaptureBtn.disabled = true;


  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480 }
    });


    video.srcObject = stream;


    // QUAN TRỌNG: đợi metadata
    await new Promise((resolve) => {
      video.onloadedmetadata = () => resolve();
    });


    await video.play();


    // delay thêm để camera ổn định
    await new Promise(r => setTimeout(r, 500));


    captureImagesLoop();


  } catch (err) {
    alert("Camera error: " + err.message);
    startCaptureBtn.disabled = false;
  }
});


/* =========================
   CAPTURE LOOP (STABLE)
========================= */
async function captureImagesLoop() {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");


  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;


  images = [];
  captured = 0;


  while (captured < maxImages) {


    // ensure frame ready
    if (video.readyState < 2) {
      await new Promise(r => setTimeout(r, 50));
      continue;
    }


    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);


    const blob = await new Promise(res =>
      canvas.toBlob(res, "image/jpeg", 0.9)
    );


    if (blob) {
      images.push(blob);
      captured++;


      captureStatus.innerText = `Captured ${captured} / ${maxImages}`;
      progressBar.style.width = `${(captured / maxImages) * 100}%`;
    }


    // FPS ổn định hơn
    await new Promise(r => setTimeout(r, 150));
  }


  await uploadImages();


  stopCamera();
}


/* =========================
   UPLOAD
========================= */
async function uploadImages() {
  const form = new FormData();


  form.append("student_id", student_id);


  images.forEach((b, i) => {
    form.append("images[]", b, `img_${i}.jpg`);
  });


  const resp = await fetch("/upload_face", {
    method: "POST",
    body: form
  });

  const data = await resp.json();
  const alertBox = document.getElementById("uploadAlert");

  if (resp.ok && data.status === "ok") {
    alertBox.classList.remove("d-none", "alert-danger");
    alertBox.classList.add("alert-success");
    alertBox.innerText = data.message || `Upload thành công (${data.saved} ảnh)`;
    addStudentBtn.disabled = false;
  } else {
    alertBox.classList.remove("d-none", "alert-success");
    alertBox.classList.add("alert-danger");
    alertBox.innerText = data.message || "Upload thất bại. Vui lòng thử lại.";
    addStudentBtn.disabled = true;
  }
}


/* =========================
   STOP CAMERA
========================= */
function stopCamera() {
  if (stream) {
    stream.getTracks().forEach(t => t.stop());
  }
}

