"""
Originally from: https://gist.github.com/unbracketed/3380407
Exports Issues from a specified repository to a CSV file
Uses basic authentication (Github username + password) to retrieve Issues
from a repository that username has access to. Supports Github API v3.

JIRA issue fields:
https://confluence.atlassian.com/adminjiracloud/issue-fields-and-statuses-776636356.html

Other JIRA import fields:
https://confluence.atlassian.com/adminjiracloud/importing-data-from-csv-776636762.html

Post here: https://github.com/ZenHubIO/support/issues/1070

        
        Jira fields         -  Current fields 
        
        Flag                - 
        Issue color         - 
        Start date          - 
        Issue Summary       - Summary
        Issue Type          - Type
        Issue Description   - Description + Comments + Created_at + Updated_at 
                                    + GitHub issue link + PR link + Status + Epic IDs
        Labels              - Labels
        Due Date            - Resolved
        Assignee            - Assignee - (needs to be an existing user else it fails)
        Parent ID           - would be epic can't do as there is no existing issue link
        
        
"""
import csv
import datetime
import requests
import time

GITHUB_USER = ''
GITHUB_AUTH_TOKEN = '' 
AUTH = (GITHUB_USER, GITHUB_AUTH_TOKEN)

ZENHUB_AUTH_TOKEN = ''
ZENHUB_HEADERS = { 'X-Authentication-Token': ZENHUB_AUTH_TOKEN }

# format is username/repo
REPOS = [
         ]  

# if specified, only import issues with this label
FILTER_LABEL = '' 

# "github_user": "jira_email"
ASSIGNEE_GITHUB_JIRA_MAPPING = {
}
   
   
def get_github_repo_id(repo):
    return requests.get(f"https://api.github.com/repos/{repo}", auth=AUTH).json()['id']


def iterate_pages(repository):
    """
    Make request for 100 issues starting from the first page until the last page is reached
    Every request text is appended to 'results'
    :return JSON object with all issues
    """
    results = []
    page_number = 1
    # per_page can be moved into a var in case you need less than 100 issues per request
    issues = 'https://api.github.com/repos/{}/issues?state=all&page={}&per_page=100'.format(repository, page_number)
    request = requests.get(issues, auth=AUTH)
    results.append(request.json())
    
    # make requests until the 'last' url is reached and increase the page number by 1 for each request
    while 'last' in request.headers.get('link', []) and 'next' in request.headers.get('link', []):
        page_number += 1
        issues = 'https://api.github.com/repos/{}/issues?state=all&page={}&per_page=100'.format(repository, page_number)
        request = requests.get(issues, auth=AUTH)
        results.append(request.json())
        print(request.headers['link'])
    else:
        print("No more pages")
    return results


def get_comments_max_nr(total_result):
    """
    Get maximum number of comments for one issue in order to write header columns when creating the CSV file
    :return: count of the max comments per issue
    """
    comments_list = []
    for result in total_result:
        for issue in result:
            if issue.get('pull_request') is None:
                if issue['comments'] > 0:
                    comments_list.append(issue['comments'])
    print(f"max comments = {max(comments_list)}")
    return max(comments_list)


def get_labels_nr(total_result):
    """
    Get number of labels for the repo. Used to write header columns when creating the CSV file
    Appends each unique label found to 'labels_list'
    :returns length of the labels_list
    """
    labels_list = []
    for result in total_result:
        for issue in result:
            if issue.get('pull_request') is None:
                for label in issue['labels']:
                    if label is not None:
                        # Check if the label name is already appended to 'labels_list'
                        if label['name'] not in labels_list:
                            labels_list.append(label['name'])
    print(f"labels number = {len(labels_list)}")
    return len(labels_list)

def get_epics(repos):
    """
    Get epics for each issue in the repo (zenhub doesn't allow to get epic per issue direclty 
    so we need to do this the other way around). We need to do it on all repos at once 
    because epics are cross-repos
    """
    issues_epics = {}
    for repo in repos:
        repo_id = get_github_repo_id(repo)
        zenhub_request = requests.get(
            f'https://api.zenhub.com/p1/repositories/{repo_id}/epics', headers=ZENHUB_HEADERS)
        repo_epics = zenhub_request.json().get('epic_issues', [])    
        
        print(f"Getting all issues and epics for {repo}: this might take a while")
        for repo_epic in repo_epics:
            zenhub_request = requests.get(
                f"https://api.zenhub.com/p1/repositories/{repo_id}/epics/{repo_epic['issue_number']}", headers=ZENHUB_HEADERS)
            time.sleep(1.5)
            for k in zenhub_request.json()['issues']:
                if k.get('issue_url'):
                    issues_epics.setdefault(k['issue_url'],[]).append(repo_epic['issue_url'])
    return issues_epics


def write_issues(results, repo, issues_epics):
    repo_id = get_github_repo_id(repo)
           
    for count, page in enumerate(results):
        print(f"start {count + 1}/{len(results)} batch")
        for issue in page:

            # We're only importing active issues
            # if issue.get('state') == 'closed':
            #     continue
            
            # Only import issues tagged with a filter label
            label_names = [label.get('name') for label in issue.get('labels', [])]
            if FILTER_LABEL and FILTER_LABEL not in label_names:
                continue

            issue_type = None
            issue_resolution = None
            issue_milestone = None
            resolved_at = None
            assignee = None
            description = ""
            pull_request = ""
            
            # filter only issues that are not pull requests
            if issue.get('pull_request') is None:
                issue_number = issue['number']
                
                # make request to zenhub with the issue number
                zenhub_request = requests.get(
                    'https://api.zenhub.com/p1/repositories/{}/issues/{}'.format(repo_id, issue_number),
                    headers=ZENHUB_HEADERS)
                
                # avoid hitting API limit of 100 req/s
                time.sleep(1.5)
                zenhub_json_object = zenhub_request.json()

                # As of 03.2020, JIRA does not create "Refactoring" and "Task" issues types, instead it makes them "Story" types.
                # It is advised to leave the "Refactoring" label and do a data migration inside of JIRA to remove the label and convert the issue type
                if zenhub_json_object.get('is_epic'):
                    issue_type = 'Epic'
                elif 'bug' in [label['name'] for label in issue['labels']]:
                    issue_type = 'Bug'
                elif 'refactor' in [label['name'] for label in issue['labels']]:
                    issue_type = 'Refactoring'
                else:
                    issue_type = 'Task'

                issue_status = zenhub_json_object['pipeline']['name']
                
                if zenhub_json_object.get('estimate'):
                    issue_estimation = zenhub_json_object['estimate']['value']
                else:
                    issue_estimation = ''

                if issue.get('assignee') is not None:
                    assignee_gh = issue['assignee']['login']
                    assignee = ASSIGNEE_GITHUB_JIRA_MAPPING.get(assignee_gh, "")
                    

                reporter = issue['user']['login']

                if issue.get('milestone') is not None:
                    issue_milestone = issue['milestone']['title']
                
                
                epics = issues_epics.get(issue['html_url'], [])
                
                # Transform dates to a format that can be parsed by Jira
                # Java Format (used by Jira) "dd/MMM/yy h:mm a" == "14/Nov/18 10:39 AM"
                # Python = "%d/%b/%y %l:%M %p"
                date_format_rest = '%Y-%m-%dT%H:%M:%SZ'
                date_format_jira = '%d/%b/%y %l:%M %p'
                date_created = datetime.datetime.strptime(issue['created_at'], date_format_rest)
                created_at = date_created.strftime(date_format_jira)

                date_updated = datetime.datetime.strptime(issue['updated_at'], date_format_rest)
                updated_at = date_updated.strftime(date_format_jira)

                if issue.get('closed_at'):
                    date_resolved = datetime.datetime.strptime(issue['closed_at'], date_format_rest)
                    resolved_at = date_resolved.strftime(date_format_jira)

                # Get pull request
                if issue.get('pull_request'):
                    pull_request = issue['pull_request']['url']
                    
                if issue['body']:
                # Imported markdown doesn't look good in JIRA, remove the headers
                    description = issue['body'].replace('#', '').replace('##', '').replace('###', '').strip() + '\n\n'

                # Append comments to description. JIRA import breaks when trying to import comments
                comments = []
                if issue['comments'] > 0:
                    comments_request = requests.get(issue['comments_url'], auth=AUTH)
                    for comment in comments_request.json():
                        issue_comments = 'Username: {}\n{};'.format(comment['user']['login'], comment['body'])
                        comments.append(issue_comments)
                comments = comments + [''] * (comments_max_nr - len(comments))
                description += '\n'.join(comments)
                
                epics_list = '\nEpic: '.join(epics)
                # Add other info to description
                description += f"""
                
                Moved from ZenHub
                
                Original ticket: {issue['html_url']}
                Created at: {created_at}
                Updated at: {updated_at}
                Last status: {issue_status}
                Pull request: {pull_request}
                {epics_list}                
                """

                # Add a label `imported_datetime`. Makes it easier to identify batch imported issues.
                labels_list = ['imported_{}'.format(datetime.datetime.now().strftime('%d.%m.%y-%H:%M'))]
                labels = issue['labels']
                for label in labels:
                    label_name = label['name']
                    labels_list.append(label_name)

                labels_list = labels_list + [None] * (labels_max_nr - len(labels_list))

                if issue_status == 'Closed':
                    issue_resolution = 'Done'

                csvout.writerow([
                    issue['title'].strip(),
                    issue_type,
                    issue_status,
                    # issue_resolution,
                    # issue_milestone,
                    description,
                    assignee,
                    reporter, # not in Jira
                    created_at, # not in Jira
                    updated_at, # not in Jira
                    resolved_at, # not in Jira
                    issue_estimation, # not in Jira
                    *labels_list,  # labels (multiple labels in multiple columns)
                ])


if __name__ == '__main__':
    
    epic_issues = get_epics(REPOS)
        
    # Call and save the JSON object created by ´iterate_pages()´
    for repo in REPOS:
        print(f"start processing {repo}")
        total_result = iterate_pages(repo)
        comments_max_nr = get_comments_max_nr(total_result)
        labels_max_nr = get_labels_nr(total_result)

        # Create enough labels columns to hold max number of labels
        labels_header_list = ['Labels'] * labels_max_nr
        csvfile = '%s-issues.csv' % (repo.replace('/', '-'))
        csvout = csv.writer(open(csvfile, 'w', newline=''))

        # Write CSV Header
        csvout.writerow((
            'Summary',
            'Type',  # Need Zenhub API for this (task, epic, bug)
            'Status',  # Need Zenhub API for this (in which pipeline is located)
            # 'Resolution',  # Need Zenhub API for this (done, won't do, duplicate, cannot reproduce) - for software projects
            # 'Fix Version(s)',  # milestone
            'Description',
            'Assignee',
            'Reporter',
            'Created',
            'Updated',
            'Resolved',
            'Estimate',
            *labels_header_list,
        ))
        
        print("preparation done, start writing issues")
        write_issues(total_result, repo, epic_issues)
        print(f"repo {repo} done")
