# aws-ec2-samples
Code Forked from `aws-samples/aws-cloudfront-samples` and changed to automatically update EC2 IP ranges change and also workaround for security group ingress rules limit. 
For more detail read README.md inside folder update_security_groups_lambda.s
## update_security_groups_lambda

This AWS Lambda function is written in Python and can be used to automatically
update EC2 security group ingress rules when EC2 IP ranges change.

By subscribing this function to the SNS topic
[AmazonIpSpaceChanged](http://docs.aws.amazon.com/general/latest/gr/aws-ip-ranges.html#subscribe-notifications)
your security groups that are properly tagged will be updated accordingly.


For more information on ip-ranges.json, read the documentation on [AWS IP Address Ranges](http://docs.aws.amazon.com/general/latest/gr/aws-ip-ranges.html).

