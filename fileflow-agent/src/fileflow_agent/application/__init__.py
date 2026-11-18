"""Application layer -- use cases.

Each class is one job the user asks the agent to do, written only
against domain ports. A use case knows the order of the steps and
the failure rules; it knows nothing about FTP, SQL, or HTTP.
"""
