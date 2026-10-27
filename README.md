# Campus Equipment Borrowing and Inventory System

A custom Python & Django web application designed to streamline campus equipment requests, manage physical unit availability, prevent double-booking, and maintain real-time asset tracking.

---

## 📌 Project Overview

The system features two primary operational levels:
1. **Basic User Portal:** A custom-coded interface for user authentication, catalog browsing, multi-item request submissions, and request status tracking/cancellation.
2. **Admin Interface:** An administrative portal for inventory tracking, user management, unit allocation, physical releases, return processing, and maintenance records.

---

## 🔑 Key Features & Architecture

### 👤 Basic User Portal
* **Account & Profile Management:** User registration, sign-in, sign-out, and profile management (name and contact details). Powered by Django Authentication.
* **Equipment Catalog Browsing:** Search and view active catalog items by name/category, showing available vs. total physical units while concealing sensitive admin notes.
* **Request Submission:** Submit multi-item borrowing requests (1–3 items per request) with quantity validation and maximum loan duration limits (up to 7 days).
* **Request Tracking & Cancellation:** Real-time status tracking for active requests, due dates, and assigned units. Allows cancellation of `Pending` or `Approved` requests prior to equipment issue to release reserved units.

### 🛠️ Administrative Interface
* **Account Management:** User activation, deactivation, and request history inspection.
* **Inventory & Unit Lifecycle:** Complete tracking of physical equipment units across five invariant status states: `Available`, `Reserved`, `Issued`, `Maintenance`, and `Retired`.
* **Request Approval & Unit Allocation:** Review pending requests and atomically allocate available physical units to prevent double-booking.
* **Release & Return Operations:** Issue approved loans with borrowing slips, record unit returns, flag damaged units for maintenance, and monitor overdue loans.

---

## 🛠️ Tech Stack

* **Backend Framework:** Python 3.14.3 and Django 6.1.2 (MTV Architecture, Django ORM, Authentication, Migrations)
* **Frontend:** HTML5, CSS3, JavaScript, Bootstrap
* **Database:** SQLite for local development and classroom demonstration

---

## 🚀 Setup & Execution Guide

### 1. Prerequisites
The project was developed with Python 3.14.3 and Django 6.1.2. Install Python 3.14, then open a terminal in the folder containing `manage.py`.

### 2. Virtual Environment & Dependencies Setup
Create and activate a virtual environment in the project folder:

* **Windows:**
  ```bash
  python -m venv .venv
  .venv\Scripts\activate
  ```

* **macOS/Linux:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

Install the pinned project dependency:
```bash
python -m pip install -r requirements.txt
```

### 3. Database Migration
Apply migrations to generate database tables:
```bash
python manage.py migrate
```

### 4. Create an Admin Account and Optional Demo Data
Create your own administrative account:
```bash
python manage.py createsuperuser
```

To load fictional categories, equipment, users, requests, loans, and maintenance records, run the included seed command:
```bash
python manage.py seed_demo
```
The command creates a demo admin only if no active superuser exists, and prints generated demo-account credentials in the terminal. If you created an admin first, its password stays unchanged. Use `python manage.py seed_demo --reset` only when you intend to replace existing demo catalog, request, and basic-user data.

### 5. Start the Server
Run the local development server:
```bash
python manage.py runserver
```

Access the application in your browser:
* **Sign in:** `http://127.0.0.1:8000/accounts/login/`
* **Register:** `http://127.0.0.1:8000/accounts/register/`
* **Basic User Portal:** `http://127.0.0.1:8000/portal/`
* **Admin Interface:** `http://127.0.0.1:8000/admin/`
The root address redirects signed-in users to the page for their account role.

---

## 📊 Core Entities & Workflow Invariants

* **User & Profile:** Core authentication identity linked to active/inactive status and specific role levels.
* **Category & CatalogItem:** Organized categories of equipment and individual catalog items.
* **EquipmentUnit:** Physical inventory assets assigned unique asset codes and tracked strictly across `Available`, `Reserved`, `Issued`, `Maintenance`, or `Retired` states.
* **BorrowRequest & RequestItem:** Manages request submissions, requested quantities, purpose, and requested return dates.
* **UnitAllocation & Loan:** Handles atomic reservation upon approval, issue timestamps, loan tracking, and return processing.

---

## Automated Tests and Results

Run the Django test suite from the folder containing `manage.py`:
```bash
python manage.py test --verbosity 2
```
The test runner creates a temporary test database and prints each test result in the terminal. The GitHub Actions workflow runs the same checks on pushes and pull requests. To keep evidence, open the workflow run under the repository's **Actions** tab and download the `django-test-results` artifact.

---

## 🛡️ Security Measures

* Protection against role self-elevation across all user forms and endpoints.
* CSRF token protection on all state-changing POST requests.
* Secure password hashing using standard Django authentication algorithms.
* Strict scope checks ensuring basic users can only access or cancel their own requests.
* The checked-in settings are for local development. Before hosting the application publicly, move `SECRET_KEY` to an environment variable, set `DEBUG=False`, and configure `ALLOWED_HOSTS`.
