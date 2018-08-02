docker build -f build/Dockerfile -t test .
docker run -d -it -p 127.0.0.1:9090:9090 test 
