from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
# Create your views here.
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from courses.models import Course
from submissions.forms import (StudentClassifyForm, SubmissionFilesUploadForm,
                               SubmissionSearchForm, SyncFromForm, SyncToForm)
from submissions.models import PaperSubmission, PaperSubmissionImage, SubmissionComment
from submissions.views import _random1000

from .models import Assignment

from submissions.cluster import (crop_and_ocr, crop_images_to_text, vectorize_texts, perform_dbscan_clustering, plot_clusters_dbscan)

# Create your views here.

@login_required
def assignment_detail_view(request,  course_pk, assignment_pk):
    # course = get_object_or_404(Course, pk=course_pk)
    assignment = get_object_or_404(Assignment, pk=assignment_pk)
    qs = PaperSubmission.objects.filter(
        assignment=assignment,
        ).order_by('created')
    search_form = SubmissionSearchForm(assignment=assignment)
    upload_form = SubmissionFilesUploadForm(assignment=assignment)
    classify_form = StudentClassifyForm(no_assignment=True)
    sync_to_form = SyncToForm(no_assignment=True)
    message = ''
    message_type = 'info'
    if request.method == 'POST':
        print(request.POST)
        _mutable = request.POST._mutable
        # set to mutable
        request.POST._mutable = True
        # сhange the values you want
        request.POST['assignment'] = assignment_pk
        # set mutable flag back
        request.POST._mutable = _mutable
        if 'submit-search' in request.POST:
            print("searching")
            if assignment_pk:
                search_form = SubmissionSearchForm(request.POST.copy(), assignment=assignment)
            else:
                search_form = SubmissionSearchForm(request.POST)
            # add assignment to the form
            if search_form.is_valid():
                qs = search_form.search()
            print(len(qs))
            if len(qs) == 0:
                message = "No submissions found"
                message_type = 'warning'
            else:
                plural_s = 's' if len(qs) > 1 else ''
                message = f"{len(qs)} submission{plural_s} found"
                message_type = 'info'

        if 'submit-upload' in request.POST:            
            upload_form = SubmissionFilesUploadForm(data=request.POST, files=request.FILES)
            if upload_form.is_valid():
                print("form is valid")
                uploaded_submission_pks = upload_form.save(request)
                qs = PaperSubmission.objects.filter(pk__in=uploaded_submission_pks)
                print(len(qs))
                
                if len(qs) > 0:
                    message = f"{len(qs)} files uploaded!"
                    message_type = 'success'
                    # now use PRG pattern to avoid resubmission of form
                    # this uses HTTP 302/303 redirect
                    return redirect(
                        'assignments:detail', 
                        course_pk=course_pk, 
                        assignment_pk=assignment_pk
                        )
                else:
                    message = "No files uploaded."
                    message_type = 'danger'
        elif 'submit-classify' in request.POST:
            print("request was POST:classify")
            classify_form = StudentClassifyForm(no_assignment=False, data=request.POST)
            if classify_form.is_valid():
                print("form is valid")
                classified_submission_pks, not_classified_submission_pks = classify_form.save()
                qs_classified = PaperSubmission.objects.filter(pk__in=classified_submission_pks)
                qs_not = PaperSubmission.objects.filter(pk__in=not_classified_submission_pks)
                message = "Classified {} submissions and {} submissions were not classified".format(len(qs_classified), len(qs_not))
                if len(qs_not) > 0:
                    message_type = 'warning'
                else:
                    message_type = 'success'
        elif "submit-sync-from" in request.POST:
            # Handle ajax request and return json response with the submissions that were synced
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                print("ajax request")
                sync_from_form = SyncFromForm(no_assignment=False, data=request.POST)
                if sync_from_form.is_valid():
                    print("form is valid")
                    sync_from_form.save()
                    message = 'Sync from canvas successful'
                    message_type = 'success'
                    # return submissions that were synced as json
                    submissions = PaperSubmission.objects.filter(assignment=assignment)
                    submissions = submissions.order_by('created')
                    submissions = submissions.values('pk', 'canvas_id', 'canvas_url')
                    submissions = list(submissions)
                    
                    return JsonResponse({'submissions': submissions})
                else:
                    print(sync_from_form.errors)
                    return JsonResponse({'error': 'form is not valid'})
            else:
                print("not ajax request")
                return JsonResponse({'error': 'not ajax request'})

        elif "submit-sync-to" in request.POST:
            print("request was POST:sync-to")
            sync_to_form = SyncToForm(
                no_assignment=False,
                request_user=request.user, 
                data=request.POST)
            if sync_to_form.is_valid():
                print("form is valid")
                sync_to_form.save()
                message = 'Sync to canvas successful'
                message_type = 'success'
            else:
                print(sync_to_form.errors)
    return render(
        request,
        'assignments/detail.html',
        {'assignment': assignment, 
        'qs': qs,
        "search_form": search_form,
        'classify_form': classify_form,
        'upload_form': upload_form,
        'sync_to_form': sync_to_form,
        "random_num": _random1000,
        'message': message,
        'message_type': message_type,
        'course_pk': course_pk,
        'assignment_pk': assignment_pk,
        })

@login_required
def cluster_view(request, course_pk, assignment_pk):
    # if the request is POST, then cluster the submissions
    if request.method == 'POST':
        assignment = get_object_or_404(Assignment, pk=assignment_pk)
        # select submissions to cluster from model submissions.PaperSubmission
        # get the submissions from the database
        submissions = PaperSubmission.objects.filter(assignment=assignment)
        submissions_image = PaperSubmissionImage.objects.filter(submission__in=submissions, page=3)
        # get the items in the field
        col_names = [field.name for field in submissions_image.model._meta.get_fields()]
        images = []
        for image in submissions_image:
            images.append(image.image.url)

        # use the crop_images_to_text function to get the text from the images
        texts = crop_images_to_text(images)

        # vectorize the text
        print("vectorizing texts")
        X = vectorize_texts(texts)
        # cluster the text
        print("clustering images")
        dbscan, cluster_labels = perform_dbscan_clustering(X)
        # add the cluster labels to the database
        for i, image in enumerate(submissions_image):
            image.cluster = cluster_labels[i]
            image.save()

        # renew the submissions_image queryset after the above changes
        submissions_image = PaperSubmissionImage.objects.filter(submission__in=submissions, page=3)

        # get the images for each cluster
        cluster_types = len(set(cluster_labels))
        # separate out the outliers
        if -1 in cluster_labels:
            cluster_types -= 1
        cluster_images = [0]*cluster_types
        for image in submissions_image:
            for i in range(cluster_types):
                if image.cluster == i and cluster_images[i] == 0:
                    # get the image url
                    cluster_images[i] = image.image.url
                    break
        # context
        context = {'assignment': assignment, 'submissions': submissions, 
        'submissions_image': submissions_image, 'cluster_labels': cluster_labels, 
        'cluster_images': cluster_images, 'course_pk': course_pk, 'assignment_pk': assignment_pk}
        
        return render(request, 'assignments/cluster.html', context)
    
    # send the user to the assignment detail page
    return redirect('assignments:detail', course_pk=course_pk, assignment_pk=assignment_pk)


@login_required
def cluster_submission(request, course_pk, assignment_pk):
    # if the request is POST
    if request.method == 'POST':
        print("request was POST")
        # get the assignment
        assignment = get_object_or_404(Assignment, pk=assignment_pk)
        # get all the data from the POST request
        data = request.POST
        # convert this queryDict data to a dictionary
        data = data.dict()
        for i in range(len(data)-1):
            print(data['clusterText1-'+str(i)])
        
        # get the submissions from the database
        submissions = PaperSubmission.objects.filter(assignment=assignment)
        submissions_image = PaperSubmissionImage.objects.filter(submission__in=submissions, page=3)
        # get the each of the submissions
        for submission in submissions:
            # get the cluster label for this submission
            cluster_label = submissions_image.filter(submission=submission)[0].cluster
            if cluster_label != -1:
                # get the text for this cluster
                text = data['clusterText1-'+str(cluster_label)]
                # create a new submission comment
                submission_comment = SubmissionComment(paper_submission=submission, text=text)
                submission_comment.save()
        
    return redirect('assignments:detail', course_pk=course_pk, assignment_pk=assignment_pk)


