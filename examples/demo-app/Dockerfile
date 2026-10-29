# Two stages, so the final image is just one static binary.
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -o /app main.go

FROM scratch
COPY --from=build /app /app
EXPOSE 8080
ENTRYPOINT ["/app"]
