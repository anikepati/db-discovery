Building a FastAPI application that runs Playwright/Chromium on Red Hat OpenShift (OCP) requires careful handling of permissions. OpenShift runs containers with a random high-range UID for security, which can break browsers like Chromium that expect specific user permissions or sandbox access.

Here is a streamlined guide to building and deploying this.

## 1. The FastAPI Application (`main.py`)

This script uses Playwright’s asynchronous API to navigate to a URL and return the screenshot as a streaming response.

```python
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import async_playwright
import io

app = FastAPI()

@app.get("/getscreenshot")
async def get_screenshot(url: str = Query(..., description="The URL to capture")):
    async with async_playwright() as p:
        # 'chromium' is the best choice for speed and compatibility
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"] # Required for OpenShift
        )
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            screenshot_bytes = await page.screenshot(full_page=True)
            await browser.close()
            
            return StreamingResponse(io.BytesIO(screenshot_bytes), media_type="image/png")
        
        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

```

---

## 2. The Dockerfile (Enterprise Red Hat Compatible)

To run on OCP, it is best to use a base image that already has the complex system dependencies (like `libatk`, `libX11`, etc.) installed.

> **Note:** We use the Microsoft Playwright image as a base because it is the most stable for this use case. To satisfy OpenShift security, we must ensure the `ms-playwright` folder has group-root permissions.

```dockerfile
# Use the official Playwright image which includes Python and Browser dependencies
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Install FastAPI and Uvicorn
RUN pip install --no-cache-dir fastapi uvicorn playwright

# OpenShift Compatibility: Playwright downloads browsers to ~/.cache/ms-playwright
# We need to ensure the random OpenShift user can access these
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN mkdir -p /app/pw-browsers && \
    playwright install chromium && \
    chmod -R 775 /app/pw-browsers && \
    chgrp -R 0 /app/pw-browsers

COPY . .

# Expose port 8080 (standard for OpenShift)
EXPOSE 8080

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

```

---

## 3. Deployment to OpenShift (OCP)

Since you are in an enterprise environment, follow these steps via the `oc` CLI:

1. **Create a New Project:**
```bash
oc new-project screenshot-service

```


2. **Build and Push the Image:**
(Replace with your internal registry URL, e.g., Quay or the OCP internal registry)
```bash
oc new-build --binary --name=screenshot-api
oc start-build screenshot-api --from-dir=. --follow

```


3. **Expose the Service:**
```bash
oc expose svc/screenshot-api

```



---

## 4. Key Enterprise Considerations

* **Proxy Settings:** If your OCP cluster is behind a corporate proxy, you must pass `HTTP_PROXY` and `HTTPS_PROXY` as environment variables in your deployment so Playwright can reach the external URL.
* **Security Context Constraints (SCC):** Standard OCP namespaces use the `restricted` SCC. The `--no-sandbox` flag in the Python code is **mandatory** because containers cannot create new user namespaces for the Chrome sandbox without elevated permissions (`privileged` SCC), which enterprise admins rarely grant.
* **Resource Limits:** Chromium is memory-intensive. Ensure your Deployment configuration has at least **1GiB to 2GiB of RAM** to avoid OOM (Out of Memory) kills during page rendering.

**Would you like me to help you write a YAML manifest for the OpenShift Deployment and Route?**
##Dockerfile
# Use the Red Hat UBI 9 Python image (highly trusted in enterprise environments)
FROM registry.access.redhat.com/ubi9/python-311

# Switch to root to install system-level packages
USER root

# Install missing C++ and rendering libraries required by headless Chromium on RHEL
RUN dnf install -y \
    alsa-lib \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    nss \
    libdrm \
    mesa-libgbm \
    && dnf clean all

WORKDIR /app

# Install Python packages (FastAPI, Uvicorn, Playwright)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OpenShift Compatibility: Set Playwright path and give group-root permissions
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN mkdir -p /app/pw-browsers && \
    playwright install chromium && \
    chmod -R 775 /app/pw-browsers && \
    chgrp -R 0 /app/pw-browsers

COPY . .

# Switch back to the default non-root user (1001) provided by the UBI image
USER 1001

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
##Dockerfile
# Use the official Python image (Debian Bookworm)
FROM python:3.11-bookworm

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install OS-level dependencies for Chromium using Playwright's native tool
RUN playwright install-deps chromium

# OpenShift Compatibility: Set Playwright path and give group-root permissions
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN mkdir -p /app/pw-browsers && \
    playwright install chromium && \
    chmod -R 775 /app/pw-browsers && \
    chgrp -R 0 /app/pw-browsers

COPY . .

# Create and switch to a non-root user for OpenShift security compliance
RUN useradd -u 1001 -g 0 -m openshiftuser
USER 1001

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]


fastapi>=0.104.0
uvicorn>=0.24.0
playwright>=1.40.0
-----------
# Use the official Python image (Debian Bookworm)
FROM python:3.11-bookworm

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install OS-level dependencies for Chromium using Playwright's native tool
RUN playwright install-deps chromium

# OpenShift Compatibility: Set Playwright path and give group-root permissions
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN mkdir -p /app/pw-browsers && \
    playwright install chromium && \
    chmod -R 775 /app/pw-browsers && \
    chgrp -R 0 /app/pw-browsers

COPY . .

# Create and switch to a non-root user for OpenShift security compliance
RUN useradd -u 1001 -g 0 -m openshiftuser
USER 1001

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
------------------------
# Use the Red Hat UBI 9 Python image (highly trusted in enterprise environments)
FROM registry.access.redhat.com/ubi9/python-311

# Switch to root to install system-level packages
USER root

# Install missing C++ and rendering libraries required by headless Chromium on RHEL
RUN dnf install -y \
    alsa-lib \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    nss \
    libdrm \
    mesa-libgbm \
    && dnf clean all

WORKDIR /app

# Install Python packages (FastAPI, Uvicorn, Playwright)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OpenShift Compatibility: Set Playwright path and give group-root permissions
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN mkdir -p /app/pw-browsers && \
    playwright install chromium && \
    chmod -R 775 /app/pw-browsers && \
    chgrp -R 0 /app/pw-browsers

COPY . .

# Switch back to the default non-root user (1001) provided by the UBI image
USER 1001

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
