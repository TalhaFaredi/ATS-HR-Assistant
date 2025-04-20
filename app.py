import os
import base64
import re
import json
import shutil
import google.generativeai as genai
from flask import Flask, render_template, request
from neo4j import GraphDatabase
from rapidfuzz import fuzz

app = Flask(__name__)

# Neo4j Database Credentials
NEO4J_URI = "neo4j+s://1485fcc8.databases.neo4j.io"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "7et_hGlV236-otIdjy-Zf-ZwlLbpi9YBiU2yCo0pw4U"
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# Configuration
ALLOWED_EXTENSIONS = {'pdf'}
TEMP_FOLDER = os.path.join(os.getcwd(), "temp")
FILTERED_RESUMES_FOLDER = os.path.join(os.getcwd(), "filtered_resumes")
GEMINI_API_KEY = 'AIzaSyA-SkTTnlt1KuubFwiGgn-cK7kg-MV4kiU'

# Ensure folders exist
for folder in [TEMP_FOLDER, FILTERED_RESUMES_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Function to check file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Query Neo4j to find related skills
def query_neo4j(skills):
    node_mapping = {}
    try:
        with driver.session() as session:
            for skill in skills:
                query = """
                MATCH (dl:Category)<-[:HAS_SUBCATEGORY*]-(parent:Category)
                WHERE dl.name =~ '(?i).*' + $skill + '.*' 
                RETURN parent.name AS Parent_Node, dl.name AS Connected_Node
                """
                result = session.run(query, skill=skill)
                node_mapping[skill] = [record["Parent_Node"] for record in result] or ["No record found"]
    except Exception as e:
        print(f"Error querying Neo4j: {e}")
        return {}
    return node_mapping

# Function to apply fuzzy matching for locations
def is_fuzzy_match(resume_location, hr_locations, threshold=80):
    return any(fuzz.partial_ratio(resume_location.lower(), loc.lower()) > threshold for loc in hr_locations)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Get HR requirements from form
        experience = int(request.form.get('experience', 0))
        location = request.form.get('location', '').split(",") if request.form.get('location') else []
        skills = request.form.get('skills', '').split(",") if request.form.get('skills') else []

        Hr_requirements = {'experience': experience, 'location': location, 'skills': skills}

        # Save uploaded PDF files
        uploaded_files = request.files.getlist('resume')
        for file in uploaded_files:
            if allowed_file(file.filename):
                file.save(os.path.join(TEMP_FOLDER, file.filename))

        intersection_results = process_uploaded_files(Hr_requirements)
        return render_template('show.html', intersection_results=intersection_results)

    return render_template('index.html')
@app.route('/process', methods=['POST'])
def process_uploaded_files(Hr_requirements):
    """Process all uploaded PDFs and filter candidates."""
    all_extracted_data = []  # Store extracted resume data

    # Process each PDF file
    for filename in os.listdir(TEMP_FOLDER):
        file_path = os.path.join(TEMP_FOLDER, filename)
        if not os.path.isfile(file_path) or not allowed_file(filename):
            continue

        print(f"Processing: {file_path}")

        # Read file and encode in base64
        with open(file_path, "rb") as f:
            doc_data = base64.standard_b64encode(f.read()).decode("utf-8")

        # Prompts for Gemini API
        prompts = {
            "skills": """Extract only the skills section. Return JSON: {"skills": ["skill1", "skill2", "skill3"]}""",
            "location": """Extract the candidate's city. Return JSON: {"location": "City"}""",
            "experience": """Calculate total experience in years. Return JSON: {"experience_years": 5}"""
        }

        # Configure Gemini API
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")

        # Extract information from resume
        extracted_data = {
            "Filename": filename,
            "Skills": parse_gemini_response(model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, prompts["skills"]]).text).get('skills', []),
            "Location": parse_gemini_response(model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, prompts["location"]]).text).get('location', 'N/A'),
            "Experience": parse_gemini_response(model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, prompts["experience"]]).text).get('experience_years', 0)
        }

        all_extracted_data.append(extracted_data)

    # Filtering resumes using fuzzy matching and experience check
    filtered_results = [
        resume for resume in all_extracted_data
        if is_fuzzy_match(resume['Location'], Hr_requirements['location'], threshold=75)
        and resume['Experience'] >= Hr_requirements['experience']
    ]

    if not filtered_results:
        print("No resumes matched experience or location criteria.")
        return {"message": "No matching results found."}

    # Fetch relevant skills from Neo4j
    final_results = {}
    for resume in filtered_results:
        node_mapping = query_neo4j(resume['Skills'])
        unique_nodes = list({node for nodes in node_mapping.values() for node in nodes if node != "No record found"})
        final_results[resume["Filename"]] = unique_nodes

    # Compare with HR's required skills
    hr_skill_nodes = list({node for nodes in query_neo4j(Hr_requirements['skills']).values() for node in nodes if node != "No record found"})

    # Find matching skills
    intersection_results = {}
    for file_name, skills in final_results.items():
        matching_skills = set(hr_skill_nodes).intersection(set(skills))
        matching_percentage = round((len(matching_skills) / len(hr_skill_nodes)) * 100, 2) if hr_skill_nodes else 100
        intersection_results[file_name] = {'Matching Skills': list(matching_skills), 'Matching Percentage': matching_percentage}

    if not intersection_results:
        print("No resumes matched the required skills.")
        return {"message": "No matching results found."}

    # Move matched resumes to "filtered_resumes" folder
    for file_name in intersection_results.keys():
        src_path = os.path.join(TEMP_FOLDER, file_name)
        dest_path = os.path.join(FILTERED_RESUMES_FOLDER, file_name)
        if os.path.exists(src_path):
            shutil.move(src_path, dest_path)
            print(f"Moved {file_name} to filtered_resumes.")

    # Clean up remaining files in TEMP_FOLDER
    for pdf_file in os.listdir(TEMP_FOLDER):
        os.remove(os.path.join(TEMP_FOLDER, pdf_file))

    return intersection_results

def parse_gemini_response(response_text):
    """Extract JSON data from Gemini API response."""
    try:
        cleaned_response = re.sub(r'```json|```', '', response_text).strip()
        return json.loads(cleaned_response)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON format received"}

if __name__ == '__main__':
    app.run(debug=True)
