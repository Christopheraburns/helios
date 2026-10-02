Put the two ComfyUI workflows here, exported in **API format**:

- `qwen_image_api.json`: Qwen-Image text-to-image (for the `front` view).
- `qwen_image_edit_api.json`: Qwen-Image-Edit, image + instruction to image (for `angle` and `damaged`).

In ComfyUI: open the workflow, then **Workflow → Export (API)**. In older ComfyUI versions,
enable "Dev mode options" in the settings first, then use **Save (API Format)**. A UI-format
export (with `nodes` and `links`) will not work; `comfyui_runner.py inspect` says so.
