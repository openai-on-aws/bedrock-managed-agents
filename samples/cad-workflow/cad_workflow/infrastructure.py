"""Render a portable CloudFormation template from the reviewed local source."""

import base64
import gzip
import json
from pathlib import Path

from cad_workflow.backends import get_backend
from cad_workflow.contract import digest, json_bytes

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("__init__.py", "contract.py", "backends/__init__.py", "worker.py")


def ref(name):
    return {"Ref": name}


def sub(text):
    return {"Fn::Sub": text}


def attribute(name, field):
    return {"Fn::GetAtt": [name, field]}


def resource(kind, properties, why, must=(), **options):
    return {
        "Type": kind, "Properties": properties,
        "Metadata": {"com.aws.cloudformation.Context": {"why": why, "must": list(must)}},
        **options,
    }


def statement(actions, resources, **fields):
    return {"Effect": "Allow", "Action": actions, "Resource": resources, **fields}


def policy(statements):
    return {"Version": "2012-10-17", "Statement": statements}


def trust(service, conditions=None):
    value = {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}
    if conditions:
        value["Condition"] = conditions
    return policy([value])


def role(service, statements, why, conditions=None, managed=()):
    properties = {
        "AssumeRolePolicyDocument": trust(service, conditions),
        "Policies": [{"PolicyName": "Application", "PolicyDocument": policy(statements)}],
    }
    if managed:
        properties["ManagedPolicyArns"] = list(managed)
    return resource("AWS::IAM::Role", properties, why, ["No agent-controlled role, command or resource selection."])


def package_sources(backend):
    files = {f"cad_workflow/{name}": (ROOT / "cad_workflow" / name).read_text()
             for name in (*SOURCE_FILES, *backend.SOURCE_FILES)}
    for phase, module in (("author", backend.AUTHOR_MODULE), ("verifier", backend.VERIFIER_MODULE)):
        files[f"{phase}.FCMacro"] = (
            "import sys\nsys.path.insert(0, '/opt/cad-workflow')\n"
            f"from {module} import main\nmain()\n"
        )
    files["run_worker.py"] = (
        "import sys\nsys.path.insert(0, '/opt/cad-workflow')\n"
        "from cad_workflow.worker import main\nraise SystemExit(main())\n"
    )
    data = json_bytes(files)
    return base64.b64encode(gzip.compress(data, mtime=0)).decode(), digest(data)


def lambda_source():
    contract = (ROOT / "cad_workflow/contract.py").read_text()
    broker = (ROOT / "cad_workflow/broker.py").read_text()
    imported = "from cad_workflow.contract import digest, job_id, json_bytes, request"
    if broker.count(imported) != 1:
        raise ValueError("Broker packaging import contract changed")
    return contract + "\n" + broker.replace(imported, "")


def render(use_existing_network=False, backend_name="freecad"):
    backend = get_backend(backend_name)
    bundle, source_sha = package_sources(backend)
    bootstrap = (ROOT / "bootstrap.sh").read_text().replace("@@SOURCE_BUNDLE@@", bundle).replace("@@SOURCE_SHA@@", source_sha)
    if len(bootstrap.encode()) > 16 * 1024:
        raise ValueError("EC2 user data exceeds 16 KiB; use a source artifact before adding more code")
    tags = [{"Key": "Project", "Value": ref("AWS::StackName")},
            {"Key": "ManagedBy", "Value": "bma-cad-sample"}]
    logs = sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/cad/${AWS::StackName}/*")
    bucket_objects = sub("${Artifacts.Arn}/jobs/*")
    worker_arn = sub("arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${Worker}")
    document_arn = sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:document/${CadCommand}")
    own_account = {"StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")}}
    resources = {}
    resources["Artifacts"] = resource("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        },
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "VersioningConfiguration": {"Status": "Enabled"},
        "Tags": tags,
    }, "Retain native files and verification evidence after compute is stopped or removed.",
        ["Private, encrypted and versioned; stack deletion must preserve customer data."],
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    resources["ArtifactPolicy"] = resource("AWS::S3::BucketPolicy", {
        "Bucket": ref("Artifacts"),
        "PolicyDocument": policy([{
            "Effect": "Deny", "Principal": "*", "Action": "s3:*",
            "Resource": [attribute("Artifacts", "Arn"), sub("${Artifacts.Arn}/*")],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }]),
    }, "Require TLS on artifact access.", ["Never publish the bucket."])
    resources["Vpc"] = resource("AWS::EC2::VPC", {
        "CidrBlock": "10.84.0.0/24", "EnableDnsSupport": True, "EnableDnsHostnames": True, "Tags": tags,
    }, "Dedicated pilot network with outbound package and AWS access.")
    resources["InternetGateway"] = resource("AWS::EC2::InternetGateway", {"Tags": tags},
                                          "Permit outbound HTTPS without a continuously billed NAT gateway.")
    resources["GatewayAttachment"] = resource("AWS::EC2::VPCGatewayAttachment", {
        "VpcId": ref("Vpc"), "InternetGatewayId": ref("InternetGateway"),
    }, "Attach the pilot's dedicated outbound route.")
    resources["Subnet"] = resource("AWS::EC2::Subnet", {
        "VpcId": ref("Vpc"), "CidrBlock": "10.84.0.0/26", "Tags": tags,
    }, "Worker uses a public address only for outbound connectivity; ingress stays closed.")
    resources["RouteTable"] = resource("AWS::EC2::RouteTable", {"VpcId": ref("Vpc"), "Tags": tags},
                                     "Route only this deployment's subnet.")
    resources["Route"] = resource("AWS::EC2::Route", {
        "RouteTableId": ref("RouteTable"), "DestinationCidrBlock": "0.0.0.0/0",
        "GatewayId": ref("InternetGateway"),
    }, "Bootstrap packages and SSM use outbound HTTPS.", DependsOn="GatewayAttachment")
    resources["SubnetRoute"] = resource("AWS::EC2::SubnetRouteTableAssociation", {
        "RouteTableId": ref("RouteTable"), "SubnetId": ref("Subnet"),
    }, "Bind the worker subnet to its own route table.")
    resources["WorkerSecurityGroup"] = resource("AWS::EC2::SecurityGroup", {
        "VpcId": ref("Vpc"), "GroupDescription": "No inbound traffic; management through SSM",
        "SecurityGroupIngress": [],
        "SecurityGroupEgress": [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "CidrIp": "0.0.0.0/0"}],
        "Tags": tags,
    }, "SSM initiates outbound connections; no public SSH, desktop or MCP listeners.",
        ["No ingress rules. CAD child processes additionally have no network namespace connectivity."])
    for name, suffix in (("WorkerLogs", "worker"), ("BrokerLogs", "broker")):
        resources[name] = resource("AWS::Logs::LogGroup", {
            "LogGroupName": sub("/cad/${AWS::StackName}/" + suffix),
            "RetentionInDays": 30,
        }, "Bounded retention for job and tool diagnostics.")
    resources["WorkerRole"] = role("ec2.amazonaws.com", [
        statement(["s3:PutObject", "s3:GetObject"], [bucket_objects, sub("${Artifacts.Arn}/health/*")]),
        statement(["logs:CreateLogGroup", "logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"], logs),
        # SSM's log forwarder discovers the configured group first. This
        # metadata-only API does not support log-group resource scoping.
        statement("logs:DescribeLogGroups", "*",
                  Condition={"StringEquals": {"aws:RequestedRegion": ref("AWS::Region")}}),
        statement("cloudformation:SignalResource", ref("AWS::StackId")),
    ], "Only the trusted supervisor has artifact and SSM credentials.",
        managed=[sub("arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore")])
    resources["WorkerProfile"] = resource("AWS::IAM::InstanceProfile", {"Roles": [ref("WorkerRole")]},
                                        "Attach the supervisor role to this worker.")
    resources["Worker"] = resource("AWS::EC2::Instance", {
        "ImageId": ref("AmiId"), "InstanceType": ref("InstanceType"),
        "IamInstanceProfile": ref("WorkerProfile"),
        "InstanceInitiatedShutdownBehavior": "stop",
        "CreditSpecification": {"CPUCredits": "standard"},
        "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1,
                            "InstanceMetadataTags": "disabled"},
        "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {
            "Encrypted": True, "VolumeType": "gp3", "VolumeSize": 30, "DeleteOnTermination": True}}],
        "NetworkInterfaces": [{"DeviceIndex": "0", "AssociatePublicIpAddress": True,
                               "SubnetId": ref("Subnet"), "GroupSet": [ref("WorkerSecurityGroup")]}],
        "UserData": {"Fn::Base64": sub(bootstrap)},
        "Tags": tags + [{"Key": "Name", "Value": ref("AWS::StackName")},
                       {"Key": "StopAtUtc", "Value": ref("StopAtUtc")}],
    }, "Pinned native FreeCAD on x86_64; separate author and verifier users with systemd isolation.",
        ["Encrypted disk and IMDSv2. Wait for bootstrap, then run a real native task before acceptance."],
        DependsOn=["Route", "SubnetRoute"],
        CreationPolicy={"ResourceSignal": {"Count": 1, "Timeout": "PT20M"}})
    resources["CadCommand"] = resource("AWS::SSM::Document", {
        "DocumentType": "Command", "DocumentFormat": "JSON",
        "Content": {
            "schemaVersion": "2.2", "description": "Run the bounded native CAD supervisor",
            "parameters": {"RequestBase64": {"type": "String", "allowedPattern": "^[A-Za-z0-9+/]+={0,2}$"}},
            "mainSteps": [{"action": "aws:runShellScript", "name": "NativeCad", "inputs": {
                "timeoutSeconds": "300",
                "runCommand": [
                    "set -eu",
                    ". /etc/cad-workflow.env",
                    "/opt/cad-control/bin/python -I -B /opt/cad-workflow/run_worker.py --request-base64 '{{ RequestBase64 }}'",
                ],
            }}],
        },
    }, "The broker can submit data to this fixed command, never arbitrary shell.",
        ["Base64 transport plus strict JSON validation; no caller-provided command, path or AWS target."])
    resources["BrokerRole"] = role("lambda.amazonaws.com", [
        statement("ssm:SendCommand", [worker_arn, document_arn]),
        statement("s3:GetObject", bucket_objects),
        statement("s3:ListBucket", attribute("Artifacts", "Arn")),
        statement("s3:PutObject", sub("${Artifacts.Arn}/jobs/*/request.json")),
        statement(["logs:CreateLogStream", "logs:PutLogEvents"], logs),
        statement(["xray:PutTraceSegments", "xray:PutTelemetryRecords"], "*"),
    ], "Broker dispatches only the fixed CAD document; it cannot write accepted verdicts.")
    resources["Broker"] = resource("AWS::Lambda::Function", {
        "Runtime": "python3.12", "Handler": "index.handler", "Role": attribute("BrokerRole", "Arn"),
        "Timeout": 40, "MemorySize": 256, "ReservedConcurrentExecutions": 2,
        "Code": {"ZipFile": lambda_source()},
        "Environment": {"Variables": {
            "CAD_INSTANCE": ref("Worker"), "CAD_DOCUMENT": ref("CadCommand"),
            "CAD_BUCKET": ref("Artifacts"), "CAD_LOG_GROUP": ref("WorkerLogs"),
        }},
        "LoggingConfig": {"LogGroup": ref("BrokerLogs"), "LogFormat": "JSON"},
        "TracingConfig": {"Mode": "Active"}, "Tags": tags,
    }, "Small authenticated tool adapter; no public function URL and no CAD runtime in Lambda.")
    resources["GatewayRole"] = role("bedrock-agentcore.amazonaws.com", [
        statement("lambda:InvokeFunction", attribute("Broker", "Arn")),
    ], "Gateway may invoke only this deployment's tool broker.",
        conditions={
            **own_account,
            "ArnLike": {"aws:SourceArn": sub(
                "arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/${AWS::StackName}-tools-*")},
        })
    resources["Gateway"] = resource("AWS::BedrockAgentCore::Gateway", {
        "Name": sub("${AWS::StackName}-tools"), "AuthorizerType": "AWS_IAM",
        "RoleArn": attribute("GatewayRole", "Arn"), "ProtocolType": "MCP",
        "ProtocolConfiguration": {"Mcp": {
            "SupportedVersions": ["2025-03-26"],
            "Instructions": "Create a native FreeCAD support part, then get its independently checked result.",
        }},
        "Description": "IAM-authenticated native CAD tools",
    }, "BMA authenticates using its session role; no shared MCP bearer token.",
        ["Advertise the BMA-compatible MCP protocol and require IAM."])
    schemas = [
        {"Name": "create_support",
         "Description": "Create and independently verify a native FreeCAD bracket. Fixed 160x42 mm stock, three 6.5 mm mounting bores, and a capsule blind pocket. Thickness 10..20 mm; depth >=1 mm and <=thickness-2 mm. Supply the job_id from the task. Returns accepted, failed, or pending; poll get_result when pending.",
         "InputSchema": {"Type": "object", "Properties": {
             "job_id": {"Type": "string", "Description": "The task's 32-character lowercase hexadecimal job ID."},
             "stock_thickness_mm": {"Type": "number", "Description": "Stock thickness in mm, from 10 to 20."},
             "pocket_depth_mm": {"Type": "number", "Description": "Blind pocket depth in mm; retain at least 2 mm floor."},
         }, "Required": ["job_id", "stock_thickness_mm", "pocket_depth_mm"]}},
        {"Name": "get_result", "Description": "Read the native CAD job result and artifact hashes. Pending means checks are not complete; call again after a few seconds. Never treat pending or failed as success.",
         "InputSchema": {"Type": "object", "Properties": {
             "job_id": {"Type": "string", "Description": "The original task's job ID."}},
             "Required": ["job_id"]}},
    ]
    resources["CadTools"] = resource("AWS::BedrockAgentCore::GatewayTarget", {
        "GatewayIdentifier": attribute("Gateway", "GatewayIdentifier"),
        "Name": "cad",
        "CredentialProviderConfigurations": [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
        "TargetConfiguration": {"Mcp": {"Lambda": {
            "LambdaArn": attribute("Broker", "Arn"), "ToolSchema": {"InlinePayload": schemas}}}},
    }, "Expose a small dimensional interface with no arbitrary Python or shell tool.")
    resources["SessionRole"] = role("bedrock-mantle.amazonaws.com", [
        statement("bedrock-mantle:CreateInference", "*",
                  Condition={"StringEquals": {"bedrock-mantle:Model": ref("ModelId")}}),
        statement("bedrock-agentcore:InvokeGateway", attribute("Gateway", "GatewayArn")),
    ], "BMA can invoke only the selected model and this CAD Gateway. CreateInference requires wildcard resource with a model condition.",
        conditions={**own_account, "ArnLike": {"aws:SourceArn": sub(
            "arn:${AWS::Partition}:bedrock-mantle:${AWS::Region}:${AWS::AccountId}:*")}})
    resources["StopGroup"] = resource("AWS::Scheduler::ScheduleGroup", {
        "Name": sub("${AWS::StackName}-stop"), "Tags": tags,
    }, "Own the worker's external stop schedule.")
    resources["StopRole"] = role("scheduler.amazonaws.com", [
        statement("ec2:StopInstances", worker_arn),
    ], "External timer can stop only this worker; preserve disk and S3 evidence.",
        conditions={**own_account, "ArnLike": {"aws:SourceArn": attribute("StopGroup", "Arn")}})
    resources["StopSchedule"] = resource("AWS::Scheduler::Schedule", {
        "GroupName": ref("StopGroup"), "FlexibleTimeWindow": {"Mode": "OFF"},
        "ScheduleExpression": sub("at(${StopAtUtc})"), "ScheduleExpressionTimezone": "UTC",
        "State": "ENABLED", "Target": {
            "Arn": sub("arn:${AWS::Partition}:scheduler:::aws-sdk:ec2:stopInstances"),
            "RoleArn": attribute("StopRole", "Arn"),
            "Input": sub('{"InstanceIds":["${Worker}"]}'),
            "RetryPolicy": {"MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 3},
        },
    }, "Stop pilot compute at the configured deadline even if the operator disconnects.",
        ["Scoped to the exact worker; an enabled schedule is not proof of future execution."])
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "BMA and IAM-authenticated CAD tools with a pinned FreeCAD worker, independent native checks and bounded pilot compute.",
        "Metadata": {
            "AWSToolsMetrics": {"AWSAgentToolkit": "aws-cloudformation@3"},
            "SourceSha256": source_sha,
            "com.aws.cloudformation.Context": {
                "must": ["No public ingress.", "CAD users have no AWS credentials or network.",
                         "Artifacts are private and retained.", "Do not reuse this pilot as a shared tenant boundary."],
                "ref": [{"at": "samples/cad-workflow/README.md",
                         "has": "Deployment and operating contract"}],
            },
        },
        "Parameters": {
            "AmiId": {"Type": "AWS::EC2::Image::Id", "Description": "Pinned Ubuntu 24.04 amd64 AMI for the deployment region."},
            "InstanceType": {"Type": "String", "Default": "t3.medium", "AllowedValues": ["t3.medium", "t3.large"]},
            "ModelId": {"Type": "String", "Default": "openai.gpt-6.1-sol",
                        "AllowedPattern": "openai\\.[a-zA-Z0-9.:-]+"},
            "StopAtUtc": {"Type": "String", "AllowedPattern": "\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}",
                          "Description": "One-time UTC stop deadline, usually 24 hours after deployment."},
        },
        "Resources": resources,
        "Outputs": {
            "CadBackend": {"Value": backend.NAME, "Description": "Deployment-selected native CAD backend."},
            "WorkerId": {"Value": ref("Worker"), "Description": "Native CAD instance; manage through SSM."},
            "ArtifactBucket": {"Value": ref("Artifacts"), "Description": "Private native artifacts and checker evidence."},
            "GatewayArn": {"Value": attribute("Gateway", "GatewayArn"), "Description": "BMA MCP connector ARN."},
            "GatewayUrl": {"Value": attribute("Gateway", "GatewayUrl"), "Description": "IAM-authenticated MCP endpoint."},
            "SessionRoleArn": {"Value": attribute("SessionRole", "Arn"), "Description": "Pass this role when creating the BMA session."},
            "ModelId": {"Value": ref("ModelId"), "Description": "Model permitted by the session role."},
            "StopAtUtc": {"Value": ref("StopAtUtc"), "Description": "Configured worker stop deadline in UTC."},
            "SourceSha256": {"Value": source_sha, "Description": "Bundled worker source digest."},
        },
    }
    if use_existing_network:
        for name in ("Vpc", "InternetGateway", "GatewayAttachment", "Subnet",
                     "RouteTable", "Route", "SubnetRoute"):
            resources.pop(name)
        template["Parameters"]["ExistingVpcId"] = {
            "Type": "AWS::EC2::VPC::Id", "Description": "Existing VPC; its routing is not changed."}
        template["Parameters"]["ExistingSubnetId"] = {
            "Type": "AWS::EC2::Subnet::Id",
            "Description": "Subnet in that VPC with an existing Internet Gateway route for outbound HTTPS."}
        resources["WorkerSecurityGroup"]["Properties"]["VpcId"] = ref("ExistingVpcId")
        resources["Worker"]["Properties"]["NetworkInterfaces"][0]["SubnetId"] = ref("ExistingSubnetId")
        resources["Worker"].pop("DependsOn")
    return template
