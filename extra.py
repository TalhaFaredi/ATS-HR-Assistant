import os
import base64
import re
import json
from flask import Flask, render_template, request, redirect
import google.generativeai as genai
from neo4j import GraphDatabase
import torch
import numpy as np
import shutil


app = Flask(__name__)

# Neo4j Database Credentials
NEO4J_URI = "neo4j+s://1485fcc8.databases.neo4j.io"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "7et_hGlV236-otIdjy-Zf-ZwlLbpi9YBiU2yCo0pw4U"
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

ALLOWED_EXTENSIONS = {'pdf'}

# Configuration
TEMP_FOLDER = os.path.join(os.getcwd(), "temp")  # Temporary folder for uploaded files
GEMINI_API_KEY = 'AIzaSyA-SkTTnlt1KuubFwiGgn-cK7kg-MV4kiU'  # Replace with your Gemini API key
# Ensure the temporary folder exists
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# Function to check file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def query_neo4j(skills):
    node_mapping = {}
    try:
        with driver.session() as session:
            for skill in skills:
                query = """
                MATCH (dl:Category)<-[:HAS_SUBCATEGORY*]-(parent:Category)
                WHERE dl.name =~ '(?i).*' + $skill + '.*'  // Case-insensitive partial match
                RETURN parent.name AS Parent_Node, dl.name AS Connected_Node
                """
                result = session.run(query, skill=skill)
                node_mapping[skill] = [record["Parent_Node"] for record in result] or ["No record found"]
    except Exception as e:
        node_mapping[skill] = ["Error querying database"]
    return node_mapping
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        experience = int(request.form.get('experience', 0))  # Default to 0 if not provided
        location = request.form.get('location', '')
        location = location.split(",") if location else []
        skills = request.form.get('skills', '')
        skills = skills.split(",") if skills else []

        Hr_requirements = {
            'experience': experience,
            'location': location,
            'skills': skills
        }

        uploaded_files = request.files.getlist('resume')
        for file in uploaded_files:
            if file.filename.endswith('.pdf'):
                file_path = os.path.join(TEMP_FOLDER, file.filename)
                file.save(file_path)

        intersection_results = process_uploaded_files(Hr_requirements)

        return render_template('show.html', intersection_results=intersection_results)

    return render_template('index.html')

@app.route('/process', methods=['GET', 'POST'])
def process_uploaded_files(Hr_requirements):
    """Process all PDF files in the temp folder."""
    all_extracted_data = []  # List to store dictionaries for each file

    for filename in os.listdir(TEMP_FOLDER):
        file_path = os.path.join(TEMP_FOLDER, filename)
        if os.path.isfile(file_path) and allowed_file(filename):
            print(f"Processing file: {file_path}")
            
            # Read the PDF file and encode it in base64
            with open(file_path, "rb") as f:
                doc_data = base64.standard_b64encode(f.read()).decode("utf-8")
            
            # Define prompts for Gemini API
            skills_prompt = """
            Analyze the provided resume and extract only the skills section.
            ** Strictly extract only skills (including "Skills", "Technical Skills", "Digital Skills", etc.) **
            Return the result in JSON format:
            ```json
            { "skills": ["skill1", "skill2", "skill3",...] }
            ```
            """
            location_prompt = """
            Analyze the given resume and extract the candidate's location (city only, without the country name).  
            Return the result in JSON format:  
            ```json  
            { "location": "City" }  
            ```
            """
            experience_prompt = """
            Analyze the provided resume and calculate the total years of experience.
            Return the result in JSON format:
            ```json
            { "experience_years": 5 }
            ```
            """
            
            # Configure Gemini API
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            # Extract skills
            response_skills = model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, skills_prompt])
            skills_data = parse_gemini_response(response_skills.text)
            
            # Extract location
            response_location = model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, location_prompt])
            location_data = parse_gemini_response(response_location.text)
            
            # Calculate experience
            response_experience = model.generate_content([{'mime_type': 'application/pdf', 'data': doc_data}, experience_prompt])
            experience_data = parse_gemini_response(response_experience.text)

            # Create a dictionary for the current file
            resume_data = {
                "Filename": filename,
                "Skills": skills_data.get('skills', []),
                "Location": location_data.get('location', 'N/A'),
                "Experience": experience_data.get('experience_years', 0)
            }
            # Append the dictionary to the list
            all_extracted_data.append(resume_data)

            # Print extracted data
            print(f"File: {filename}")
            print(f"Skills: {resume_data['Skills']}")
            print(f"Location: {resume_data['Location']}")
            print(f"Experience: {resume_data['Experience']} years")
            
            # # Delete the processed file
            # os.remove(file_path)
            # print(f"Deleted file: {file_path}")
    print("-"*50)
    # Assuming Hr_requirements['location'] can be a single string or a list of strings
    filtered_results = [
        resume for resume in all_extracted_data
        if (resume['Location'] in Hr_requirements['location'] if isinstance(Hr_requirements['location'], list) else resume['Location'] == Hr_requirements['location'])
        and resume['Experience'] >= Hr_requirements['experience']
    ]
    print("Filtered Resumes:", filtered_results)

    print("-"*50)
    # Initialize an empty dictionary to store the final results
    final_results = {}

    # Iterate through each resume in the filtered_results
    for resume in filtered_results:
        filename = resume['Filename']
        skills = resume['Skills']
        
        # Call the query_neo4j function for the current set of skills
        node_mapping = query_neo4j(skills)
        
        # Flatten the list of nodes from the node_mapping dictionary
        all_nodes = []
        for skill, nodes in node_mapping.items():
            all_nodes.extend(nodes)  # Add all nodes for the current skill
        
        # Remove duplicates and filter out "No record found"
        unique_nodes = list(set(all_nodes))  # Remove duplicates
        filtered_nodes = [node for node in unique_nodes if node != "No record found"]  # Filter out unwanted entries
        
        # Save the filtered list of nodes under the corresponding filename
        final_results[filename] = filtered_nodes

    # Print the final results
    print("Final Topologies:", final_results)
    print("*"*50)
    #-----------------------------------------------------------------------------------------------------------------#

    # Extract skills from Hr_requirements
    hr_skills = Hr_requirements.get('skills', [])
    print("Hr_skills:::::::::::::::::::::::::::::::::::::::", hr_skills)
    # Query Neo4j for each skill and collect all nodes into a single list
    Hr_skills = []

    node_mapping = query_neo4j(hr_skills)
    # Flatten the list of nodes from the node_mapping dictionary
    for hr_skills, nodes in node_mapping.items():
        Hr_skills.extend(nodes)

    # Remove duplicates and filter out "No record found"
    hr_unique_nodes = list(set(Hr_skills))  # Remove duplicates
    hr_filtered_nodes = [node for node in hr_unique_nodes if node != "No record found"]  # Filter out unwanted entries

    # Print the final list of all skills/nodes
    print("Hr_topologies:", hr_filtered_nodes)
    print("******************------------------------------**********************")
    # Initialize a dictionary to store the results
    intersection_results = {}

    # Loop through each file in Final Topologies
    for file_name, skills in final_results.items():
        # Find the intersection of HR topologies and the current file's skills
        matching_skills = set(hr_filtered_nodes).intersection(set(skills))
        
        # Check if hr_filtered_nodes is not empty before calculating the percentage
        if len(hr_filtered_nodes) > 0:
            matching_percentage = round((len(matching_skills) / len(hr_filtered_nodes)) * 100, 2)
        else:
            # Handle the case where hr_filtered_nodes is empty
            matching_percentage = 0  # Default value when no HR requirements exist
        
        # Store the results in the dictionary
        intersection_results[file_name] = {
            'Matching Skills': list(matching_skills),
            'Matching Percentage': matching_percentage
        }

    # Print the results
    for file_name, result in intersection_results.items():
        print(f"File: {file_name}")
        print(f"Matching Skills: {result['Matching Skills']}")
        print(f"Matching Percentage: {result['Matching Percentage']}%")
        print("-"*50)
        print(intersection_results)
    print("******************------------------------------**********************")

    # Define the path for the filtered_resumes folder
    FILTERED_RESUMES_FOLDER = os.path.join(os.getcwd(), "filtered_resumes")

    # Ensure the filtered_resumes folder exists
    if not os.path.exists(FILTERED_RESUMES_FOLDER):
        os.makedirs(FILTERED_RESUMES_FOLDER)

    # Get the list of all PDF files in the temp folder
    all_pdf_files = [f for f in os.listdir(TEMP_FOLDER) if f.endswith('.pdf')]

    # Move matched PDFs to the filtered_resumes folder
    for file_name in intersection_results.keys():
        src_path = os.path.join(TEMP_FOLDER, file_name)
        dest_path = os.path.join(FILTERED_RESUMES_FOLDER, file_name)
        
        # Move the file if it exists in the temp folder
        if os.path.exists(src_path):
            shutil.move(src_path, dest_path)
            print(f"Moved {file_name} to filtered_resumes folder.")

    # Delete the remaining PDF files in the temp folder
    for pdf_file in all_pdf_files:
        file_path = os.path.join(TEMP_FOLDER, pdf_file)
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted {pdf_file} from temp folder.")
    #-----------------------------------------------------------------------------------------------------------------
    # return all_extracted_data
    # Render the results on the index.html page
    return intersection_results
   
   

def parse_gemini_response(response_text):
    """Parse Gemini API response and extract JSON data."""
    try:
        # Remove any leading/trailing text and parse JSON
        cleaned_response = re.sub(r'```json|```', '', response_text).strip()
        return json.loads(cleaned_response)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON format received"}

if __name__ == '__main__':
    app.run(debug=True)
#------------------------------------------
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AT Resume Screener</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
</head>
<body>
    <div class="container">
        <h1>AT Resume Screener</h1>
        <form method="POST" enctype="multipart/form-data">
            <div class="form-group">
                <label for="experience">Enter Experience:</label>
                <input type="text" id="experience" name="experience" required>
            </div>
            <div class="form-group">
                <label for="location">Enter Location:</label>
                <input type="text" id="location" name="location" required>
            </div>
            <div class="form-group">
                <label for="skills">Enter Skills:</label>
                <input type="text" id="skills" name="skills" required>
            </div>
            <div class="form-group">
                <label for="resume">Upload Resumes:</label>
                <input type="file" id="resume" name="resume" multiple>
            </div>
            <button type="submit">Submit</button>
        </form>
    </div>
</body>
</html>

#------------------------------------------
body {
    font-family: Arial, sans-serif;
    background-color: #f4f4f4;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    margin: 0;
}

.container {
    background: white;
    padding: 20px;
    border-radius: 8px;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
    width: 300px;
    text-align: center;
}

h1 {
    color: #333;
    margin-bottom: 20px;
}

.form-group {
    margin-bottom: 15px;
}

label {
    display: block;
    margin-bottom: 5px;
    color: #555;
}

input[type="text"],
input[type="file"] {
    width: 100%;
    padding: 8px;
    border: 1px solid #ddd;
    border-radius: 4px;
    box-sizing: border-box;
}

button {
    background-color: #007BFF;
    color: white;
    padding: 10px 20px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 16px;
}

button:hover {
    background-color: #0056b3;
}