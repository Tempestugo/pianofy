# Tasks - Pianofy (Iteration 2)

- [x] Grand Staff Splitting
    - [x] Update `backend/transcriber.py` to add `split_piano_grand_staff`
    - [x] Verify splitting logic using Middle C (60)
- [x] Auto-Calibration Heuristics
    - [x] Add `calibrate_heuristics` in `backend/transcriber.py`
    - [x] Update `backend/main.py` to support `auto_calibrate` Form flag and task status updates
- [x] Quantization & 1/4 Note Snapping
    - [x] Ensure 1/4 note quantization grid (divisor 1) is fully functional and stable
- [x] Frontend Binding
    - [x] Add "Auto-Calibrate" toggle to `frontend/src/App.jsx`
    - [x] Bind toggle to backend, disabling manual sliders when checked
    - [x] Update UI with calibrated values on task success
- [x] Verification & Walkthrough
    - [x] Run backend and frontend together to test end-to-end
    - [x] Generate walkthrough.md updates
